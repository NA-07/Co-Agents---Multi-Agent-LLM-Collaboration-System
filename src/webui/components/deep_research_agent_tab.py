import gradio as gr
from gradio.components import Component
from functools import partial
from datetime import datetime
from collections import deque

from src.webui.webui_manager import WebuiManager
from src.utils import config
import logging
import os
from typing import Any, Dict, AsyncGenerator, Optional, Tuple, Union
import asyncio
import json
from src.agent.deep_research.deep_research_agent import DeepResearchAgent
from src.utils import llm_provider

logger = logging.getLogger(__name__)


# ── File Upload Parsing ──────────────────────────────────────────────────────
def _parse_uploaded_files(file_list: Any) -> str:
    """
    Parse one or more uploaded files and return their textual content
    formatted for injection into the research task prompt.
    Supports: PDF, TXT, MD, DOCX, CSV and image files (as notes).
    """
    if not file_list:
        return ""
    if not isinstance(file_list, list):
        file_list = [file_list]

    parsed_chunks = []
    for file_item in file_list:
        # Gradio 5 passes file items as dicts {"path": ..., "orig_name": ...}
        if isinstance(file_item, dict):
            file_path = file_item.get("path") or file_item.get("name", "")
            orig_name = file_item.get("orig_name") or os.path.basename(str(file_path))
        elif isinstance(file_item, str):
            file_path = file_item
            orig_name = os.path.basename(file_path)
        else:
            continue

        if not file_path or not os.path.exists(str(file_path)):
            continue

        ext = os.path.splitext(str(orig_name))[1].lower()
        try:
            text = ""
            if ext == ".pdf":
                try:
                    import pdfplumber
                    with pdfplumber.open(str(file_path)) as pdf:
                        pages = [p.extract_text() for p in pdf.pages if p.extract_text()]
                    text = "\n".join(pages)
                except Exception as e:
                    text = f"[Could not extract PDF text: {e}]"
            elif ext in (".txt", ".md", ".py", ".js", ".ts", ".json",
                         ".xml", ".yaml", ".yml", ".html", ".css"):
                with open(str(file_path), "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            elif ext in (".docx", ".doc"):
                try:
                    import docx as _docx
                    doc = _docx.Document(str(file_path))
                    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
                except Exception as e:
                    text = f"[Could not extract Word document text: {e}]"
            elif ext == ".csv":
                import csv
                rows = []
                with open(str(file_path), "r", encoding="utf-8", errors="ignore") as f:
                    reader = csv.reader(f)
                    for i, row in enumerate(reader):
                        if i > 250:
                            rows.append("... (truncated at 250 rows)")
                            break
                        rows.append(", ".join(row))
                text = "\n".join(rows)
            elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                text = f"[Image file: {orig_name} — image data available for vision models]"
            else:
                text = f"[File type {ext} not directly parseable — file: {orig_name}]"

            if text:
                # Truncate very large files to avoid token overflow
                if len(text) > 15000:
                    text = text[:15000] + "\n... [File truncated at 15,000 characters]"
                parsed_chunks.append(
                    f"[Attached File: {orig_name}]\n{text}\n[End of {orig_name}]"
                )
        except Exception as e:
            parsed_chunks.append(f"[Attached File: {orig_name}] — Error reading file: {e}")

    if not parsed_chunks:
        return ""
    return "\n\n--- Attached Files Context ---\n" + "\n\n".join(parsed_chunks) + "\n--- End of Attached Files ---\n"


# ── Live UI Log Handler ──────────────────────────────────────────────────────
# A shared deque per session that the monitoring loop drains into the UI textbox.
_UI_LOG_LINES: deque = deque(maxlen=300)  # keep last 300 lines


class _UILogHandler(logging.Handler):
    """Captures agent/browser log records and appends them to _UI_LOG_LINES."""
    _INTERESTING = {
        "agent", "controller",
        "src.agent.browser_use.browser_use_agent",
        "src.agent.deep_research.deep_research_agent",
        "src.webui.components.deep_research_agent_tab",
        "browser_use.agent.service",
        "browser_use.agent.views",
        "browser_use.browser.context",
        "browser_use.controller.controller",
        "browser_use.dom",
    }

    def emit(self, record: logging.LogRecord):
        # Only capture loggers we care about (exact match or prefix match)
        is_interesting = any(
            record.name == n or record.name.startswith(n + ".") 
            for n in self._INTERESTING
        )
        # Also capture any browser_use.* logger
        if not is_interesting and record.name.startswith("browser_use"):
            is_interesting = True
        
        if not is_interesting:
            return
        try:
            ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            msg = self.format(record)
            # Strip the logger prefix — keep it short for the UI
            short = msg.split(" - ", 1)[-1] if " - " in msg else msg
            _UI_LOG_LINES.append(f"[{ts}] {short}")
        except Exception:
            pass


_ui_log_handler = _UILogHandler()
_ui_log_handler.setLevel(logging.INFO)
_ui_log_handler.setFormatter(logging.Formatter("%(name)s - %(message)s"))

# Attach to root so all child loggers flow through
logging.getLogger().addHandler(_ui_log_handler)


async def _initialize_llm(provider: Optional[str], model_name: Optional[str], temperature: float,
                          base_url: Optional[str], api_key: Optional[str], num_ctx: Optional[int] = None):
    """Initializes the LLM based on settings. Returns None if provider/model is missing."""
    if not provider or not model_name:
        logger.info("LLM Provider or Model Name not specified, LLM will be None.")
        return None
    try:
        logger.info(f"Initializing LLM: Provider={provider}, Model={model_name}, Temp={temperature}")
        # Use your actual LLM provider logic here
        llm = llm_provider.get_llm_model(
            provider=provider,
            model_name=model_name,
            temperature=temperature,
            base_url=base_url or None,
            api_key=api_key or None,
            num_ctx=num_ctx if provider == "ollama" else None
        )
        return llm
    except Exception as e:
        logger.error(f"Failed to initialize LLM: {e}", exc_info=True)
        gr.Warning(
            f"Failed to initialize LLM '{model_name}' for provider '{provider}'. Please check settings. Error: {e}")
        return None


def _read_file_safe(file_path: str) -> Optional[str]:
    """Safely read a file, returning None if it doesn't exist or on error."""
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {e}")
        return None


def _load_task_history(base_dir: str = "./tmp/deep_research"):
    """
    Scan the deep_research output directory and return a list of
    (display_label, task_id) tuples sorted newest-first.
    """
    choices = []
    if not os.path.exists(base_dir):
        return choices
    try:
        entries = sorted(
            [e for e in os.listdir(base_dir)
             if os.path.isdir(os.path.join(base_dir, e))],
            key=lambda e: os.path.getmtime(os.path.join(base_dir, e)),
            reverse=True,
        )
        for task_id in entries[:30]:  # show max 30 recent tasks
            task_dir = os.path.join(base_dir, task_id)
            has_pdf    = os.path.exists(os.path.join(task_dir, "report.pdf"))
            has_report = os.path.exists(os.path.join(task_dir, "report.md"))
            has_plan   = os.path.exists(os.path.join(task_dir, "research_plan.md"))
            status = "✅ PDF" if has_pdf else ("✅ MD" if has_report else ("📝 Plan" if has_plan else "⏳"))
            try:
                mtime = os.path.getmtime(task_dir)
                date_str = datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")
            except Exception:
                date_str = "?"
            short_id = task_id[:12] + "..." if len(task_id) > 15 else task_id
            choices.append((f"{status}  {short_id}  [{date_str}]", task_id))
    except Exception as e:
        logger.warning(f"Error loading task history: {e}")
    return choices


# --- Deep Research Agent Specific Logic ---

async def run_deep_research(webui_manager: WebuiManager, components: Dict[Component, Any]) -> AsyncGenerator[
    Dict[Component, Any], None]:
    """Handles initializing and running the DeepResearchAgent."""

    # --- Get Components ---
    research_task_comp = webui_manager.get_component_by_id("deep_research_agent.research_task")
    file_upload_comp = webui_manager.get_component_by_id("deep_research_agent.file_upload")
    resume_task_id_comp = webui_manager.get_component_by_id("deep_research_agent.resume_task_id")
    parallel_num_comp = webui_manager.get_component_by_id("deep_research_agent.parallel_num")
    save_dir_comp = webui_manager.get_component_by_id(
        "deep_research_agent.max_query")  # Note: component ID seems misnamed in original code
    start_button_comp = webui_manager.get_component_by_id("deep_research_agent.start_button")
    stop_button_comp = webui_manager.get_component_by_id("deep_research_agent.stop_button")
    markdown_display_comp = webui_manager.get_component_by_id("deep_research_agent.markdown_display")
    markdown_download_comp = webui_manager.get_component_by_id("deep_research_agent.markdown_download")
    mcp_server_config_comp = webui_manager.get_component_by_id("deep_research_agent.mcp_server_config")
    max_tasks_comp = webui_manager.get_component_by_id("deep_research_agent.max_tasks")
    task_type_display_comp = webui_manager.get_component_by_id("deep_research_agent.task_type_display")

    # --- 1. Get Task and Settings ---
    task_topic = components.get(research_task_comp, "").strip()
    uploaded_files = components.get(file_upload_comp, None)
    task_id_to_resume = components.get(resume_task_id_comp, "").strip() or None
    max_parallel_agents = int(components.get(parallel_num_comp, 1))
    max_tasks_limit = int(components.get(max_tasks_comp, 12) or 12)
    base_save_dir = components.get(save_dir_comp, "./tmp/deep_research").strip()
    safe_root_dir = "./tmp/deep_research"
    normalized_base_save_dir = os.path.abspath(os.path.normpath(base_save_dir))
    if os.path.commonpath([normalized_base_save_dir, os.path.abspath(safe_root_dir)]) != os.path.abspath(safe_root_dir):
        logger.warning(f"Unsafe base_save_dir detected: {base_save_dir}. Using default directory.")
        normalized_base_save_dir = os.path.abspath(safe_root_dir)
    base_save_dir = normalized_base_save_dir
    mcp_server_config_str = components.get(mcp_server_config_comp)
    mcp_config = json.loads(mcp_server_config_str) if mcp_server_config_str else None

    # Parse uploaded files and append to task if present
    file_context = ""
    if uploaded_files:
        try:
            file_context = _parse_uploaded_files(uploaded_files)
            if file_context:
                logger.info(f"📎 Parsed {len(uploaded_files) if isinstance(uploaded_files, list) else 1} file(s) for context injection")
        except Exception as e:
            logger.warning(f"Failed to parse uploaded files: {e}")
            gr.Warning(f"File parsing error: {e}")
    
    # Inject file context into task topic
    if file_context:
        task_topic = f"{task_topic}\n\n{file_context}"
    
    if not task_topic or (not components.get(research_task_comp, "").strip() and not file_context):
        gr.Warning("Please enter a research task or upload files with instructions.")
        yield {start_button_comp: gr.update(interactive=True)}  # Re-enable start button
        return

    # Store base save dir for stop handler
    webui_manager.dr_save_dir = base_save_dir
    os.makedirs(base_save_dir, exist_ok=True)

    # --- 2. Initial UI Update ---
    yield {
        start_button_comp: gr.update(value="⏳ Running...", interactive=False),
        stop_button_comp: gr.update(interactive=True),
        research_task_comp: gr.update(interactive=False),
        resume_task_id_comp: gr.update(interactive=False),
        parallel_num_comp: gr.update(interactive=False),
        save_dir_comp: gr.update(interactive=False),
        max_tasks_comp: gr.update(interactive=False),
        task_type_display_comp: gr.update(value="⏳ Detecting..."),
        markdown_display_comp: gr.update(value="Starting research..."),
        markdown_download_comp: gr.update(value=None, interactive=False)
    }

    agent_task = None
    running_task_id = None
    plan_file_path = None
    report_file_path = None
    last_plan_content = None
    last_plan_mtime = 0

    try:
        # --- 3. Get LLM and Browser Config from other tabs ---
        # Access settings values via components dict, getting IDs from webui_manager
        def get_setting(tab: str, key: str, default: Any = None):
            comp = webui_manager.id_to_component.get(f"{tab}.{key}")
            return components.get(comp, default) if comp else default

        # LLM Config (from agent_settings tab)
        llm_provider_name = get_setting("agent_settings", "llm_provider")
        llm_model_name = get_setting("agent_settings", "llm_model_name")
        # A5: Do NOT force temperature floor — planning/classification needs low temp for reliability
        llm_temperature = float(get_setting("agent_settings", "llm_temperature", 0.1) or 0.1)
        llm_base_url = get_setting("agent_settings", "llm_base_url")
        llm_api_key = get_setting("agent_settings", "llm_api_key")
        ollama_num_ctx = get_setting("agent_settings", "ollama_num_ctx")

        llm = await _initialize_llm(
            llm_provider_name, llm_model_name, llm_temperature, llm_base_url, llm_api_key,
            ollama_num_ctx if llm_provider_name == "ollama" else None
        )
        if not llm:
            raise ValueError("LLM Initialization failed. Please check Agent Settings.")

        # A4: Pass stealth, use_own_browser, extra args so sub-agents inherit browser settings
        stealth_enabled = get_setting("browser_settings", "stealth_mode", True)
        use_own_browser = get_setting("browser_settings", "use_own_browser", False)
        extra_browser_args_raw = get_setting("browser_settings", "extra_browser_args", "") or ""
        extra_browser_args = [a.strip() for a in extra_browser_args_raw.splitlines() if a.strip()]
        if not stealth_enabled:
            if "--no-stealth" not in extra_browser_args:
                extra_browser_args.append("--no-stealth")

        browser_config_dict = {
            "headless": get_setting("browser_settings", "headless", False),
            "disable_security": get_setting("browser_settings", "disable_security", False),
            "browser_binary_path": get_setting("browser_settings", "browser_binary_path"),
            "user_data_dir": get_setting("browser_settings", "browser_user_data_dir"),
            "window_width": int(get_setting("browser_settings", "window_w", 1280)),
            "window_height": int(get_setting("browser_settings", "window_h", 1100)),
            "use_own_browser": use_own_browser,
            "stealth_mode": stealth_enabled,
            "extra_browser_args": extra_browser_args,
        }

        # B4: Reset agent if LLM provider/model has changed since last run
        if webui_manager.dr_agent:
            prev_llm_key = getattr(webui_manager, "dr_llm_key", None)
            curr_llm_key = f"{llm_provider_name}::{llm_model_name}"
            if prev_llm_key != curr_llm_key:
                logger.info(f"LLM changed ({prev_llm_key} → {curr_llm_key}), reinitializing DeepResearchAgent.")
                webui_manager.dr_agent = None

        if not webui_manager.dr_agent:
            webui_manager.dr_agent = DeepResearchAgent(
                llm=llm,
                browser_config=browser_config_dict,
                mcp_server_config=mcp_config
            )
            webui_manager.dr_llm_key = f"{llm_provider_name}::{llm_model_name}"
            logger.info("DeepResearchAgent initialized.")
        else:
            # Always refresh browser config, LLM, and MCP config (settings may have changed)
            webui_manager.dr_agent.llm = llm
            webui_manager.dr_agent.browser_config = browser_config_dict
            webui_manager.dr_agent.mcp_server_config = mcp_config
            logger.info("DeepResearchAgent LLM and browser config refreshed.")

        # Connect UI log queue to the agent module for direct push
        from src.agent.deep_research import deep_research_agent as dra_module
        dra_module._UI_LOG_QUEUE = _UI_LOG_LINES

        # --- 5. Start Agent Run ---
        agent_run_coro = webui_manager.dr_agent.run(
            topic=task_topic,
            task_id=task_id_to_resume,
            save_dir=base_save_dir,
            max_parallel_browsers=max_parallel_agents,
            max_tasks=max_tasks_limit,
        )
        agent_task = asyncio.create_task(agent_run_coro)
        webui_manager.dr_current_task = agent_task

        # Wait briefly for the agent to start and potentially create the task ID/folder
        await asyncio.sleep(1.0)

        # Determine the actual task ID being used (agent sets this)
        running_task_id = webui_manager.dr_agent.current_task_id
        if not running_task_id:
            # Agent might not have set it yet, try to get from result later? Risky.
            # Or derive from resume_task_id if provided?
            running_task_id = task_id_to_resume
            if not running_task_id:
                logger.warning("Could not determine running task ID immediately.")
                # We can still monitor, but might miss initial plan if ID needed for path
            else:
                logger.info(f"Assuming task ID based on resume ID: {running_task_id}")
        else:
            logger.info(f"Agent started with Task ID: {running_task_id}")

        webui_manager.dr_task_id = running_task_id  # Store for stop handler

        # --- 6. Monitor Progress via research_plan.md ---
        if running_task_id:
            task_specific_dir = os.path.join(base_save_dir, str(running_task_id))
            plan_file_path = os.path.join(task_specific_dir, "research_plan.md")
            report_file_path = os.path.join(task_specific_dir, "report.md")
            logger.info(f"Monitoring plan file: {plan_file_path}")
        else:
            logger.warning("Cannot monitor plan file: Task ID unknown.")
            plan_file_path = None
        last_plan_content = None
        _step_counter = 0  # B1: live step counter
        _last_log_len = len(_UI_LOG_LINES)  # track how many log lines we've seen
        _download_enabled = False  # track if we've already enabled the download button
        while not agent_task.done():
            update_dict = {}
            update_dict[resume_task_id_comp] = gr.update(value=running_task_id)
            agent_stopped = getattr(webui_manager.dr_agent, 'stopped', False)
            if agent_stopped:
                logger.info("Stop signal detected from agent state.")
                break  # Exit monitoring loop

            # B1: Build a live status header showing current task progress
            _step_counter += 1
            _agent = webui_manager.dr_agent
            if _agent:
                _cat_idx = 0
                _task_idx = 0
                _plan = []
                try:
                    # Read progress from plan file if it exists
                    if plan_file_path and os.path.exists(plan_file_path):
                        _pf = _read_file_safe(plan_file_path)
                        if _pf:
                            # Count completed vs total tasks
                            _total = _pf.count("- [ ]") + _pf.count("- [x]") + _pf.count("- [-]")
                            _done = _pf.count("- [x]")
                            _failed = _pf.count("- [-]")
                            _pending = _pf.count("- [ ]")
                            _progress_bar = "█" * _done + "░" * _pending
                            _status_header = (
                                f"## 🔬 Research in Progress — Task ID: `{running_task_id}`\n\n"
                                f"**Progress:** {_done}/{_total} tasks complete  "
                                f"{'✅' if _done > 0 else '⏳'}  `{_progress_bar}`\n\n"
                                f"*{_failed} failed, {_pending} pending*\n\n---\n\n"
                            )
                        else:
                            _status_header = f"## ⏳ Planning research... (Task `{running_task_id}`)\n\n"
                    else:
                        _status_header = f"## ⏳ Initializing... (Task `{running_task_id}`)\n\n"
                except Exception:
                    _status_header = ""

            # Check and update research plan display
            if plan_file_path:
                try:
                    current_mtime = os.path.getmtime(plan_file_path) if os.path.exists(plan_file_path) else 0
                    if current_mtime > last_plan_mtime or _step_counter % 5 == 0:
                        plan_content = _read_file_safe(plan_file_path)
                        if plan_content and (last_plan_content is None or plan_content != last_plan_content):
                            # B1+B5: detect task type and update display
                            detected_type = "ACTION" if "Direct Task Execution" in plan_content else "RESEARCH"
                            update_dict[task_type_display_comp] = gr.update(value=f"{'\u26a1' if detected_type == 'ACTION' else '\U0001f50d'} {detected_type}")
                            update_dict[markdown_display_comp] = gr.update(value=_status_header + plan_content)
                            last_plan_content = plan_content
                            last_plan_mtime = current_mtime
                        elif plan_content is None:
                            # File might have been deleted or became unreadable
                            last_plan_mtime = 0  # Reset to force re-read attempt later
                except Exception as e:
                    logger.warning(f"Error checking/reading plan file {plan_file_path}: {e}")
                    # Avoid continuous logging for the same error
                    await asyncio.sleep(2.0)

            # ── Drain live log lines into the UI textbox ─────────────────
            live_log_comp = webui_manager.get_component_by_id("deep_research_agent.live_log")
            if live_log_comp is not None:
                current_log_len = len(_UI_LOG_LINES)
                if current_log_len != _last_log_len:
                    all_lines = list(_UI_LOG_LINES)
                    update_dict[live_log_comp] = gr.update(value="\n".join(all_lines))
                    _last_log_len = current_log_len

            # ── Check for download file early (every 3 polls) ─────────────
            if not _download_enabled and _step_counter % 3 == 0 and running_task_id:
                _task_dir_check = os.path.join(base_save_dir, str(running_task_id))
                _pdf_check = os.path.join(_task_dir_check, "report.pdf")
                _md_check  = os.path.join(_task_dir_check, "report.md")
                if os.path.exists(_pdf_check):
                    update_dict[markdown_download_comp] = gr.update(
                        value=_pdf_check, label="📄 Download PDF Report", interactive=True
                    )
                    _download_enabled = True
                elif os.path.exists(_md_check):
                    update_dict[markdown_download_comp] = gr.update(
                        value=_md_check, label="📄 Download Report (MD)", interactive=True
                    )
                    _download_enabled = True

            # Yield updates if any
            if update_dict:
                yield update_dict

            await asyncio.sleep(1.0)  # Poll every 1s — reduces CPU load, still feels live

        # --- 7. Task Finalization ---
        logger.info("Agent task processing finished. Awaiting final result...")
        final_result_dict = await agent_task  # Get result or raise exception
        logger.info(f"Agent run completed. Result keys: {final_result_dict.keys() if final_result_dict else 'None'}")

        # Try to get task ID from result if not known before
        if not running_task_id and final_result_dict and 'task_id' in final_result_dict:
            running_task_id = final_result_dict['task_id']
            webui_manager.dr_task_id = running_task_id
            task_specific_dir = os.path.join(base_save_dir, str(running_task_id))
            report_file_path = os.path.join(task_specific_dir, "report.md")
            logger.info(f"Task ID confirmed from result: {running_task_id}")

        final_ui_update = {}
        if report_file_path and os.path.exists(report_file_path):
            logger.info(f"Loading final report from: {report_file_path}")
            report_content = _read_file_safe(report_file_path)
            if report_content:
                final_ui_update[markdown_display_comp] = gr.update(value=report_content)
                # Prefer PDF download if available
                pdf_path = report_file_path.replace("report.md", "report.pdf")
                if os.path.exists(pdf_path):
                    final_ui_update[markdown_download_comp] = gr.update(
                        value=pdf_path,
                        label=f"📄 Download PDF Report",
                        interactive=True,
                    )
                else:
                    final_ui_update[markdown_download_comp] = gr.update(
                        value=report_file_path,
                        label=f"Report ({running_task_id}.md)",
                        interactive=True,
                    )
            else:
                final_ui_update[markdown_display_comp] = gr.update(
                    value="# Research Complete\n\n*Error reading final report file.*")
        elif final_result_dict and 'report' in final_result_dict:
            logger.info("Using report content directly from agent result.")
            final_ui_update[markdown_display_comp] = gr.update(value=final_result_dict['report'])
            final_ui_update[markdown_download_comp] = gr.update(value=None, label="Download Research Report",
                                                                interactive=False)
        else:
            # Check if this was a rate-limit / planning error and show a helpful message
            error_msg = (final_result_dict or {}).get('message', '')
            is_rate_limit = any(x in str(error_msg).lower() for x in ['429', 'resource exhausted', 'rate limit', 'quota'])
            if is_rate_limit:
                display_msg = (
                    "# ⚠️ Rate Limit Reached (429)\n\n"
                    "**Google Gemini free-tier quota was exhausted before the research plan could be generated.**\n\n"
                    "### What to do:\n"
                    "1. **Wait 1 minute** and try again (free tier resets per minute).\n"
                    "2. **Switch to a paid API key** with higher quotas in Agent Settings.\n"
                    "3. **Use a different model** — try `gemini-2.0-flash` if on `gemini-1.5-pro`, or switch to DeepSeek/Ollama.\n"
                    "4. **Reduce parallel agents** to 1 (already default).\n\n"
                    f"*Error detail: {error_msg}*"
                )
            else:
                logger.warning("Final report file not found and not in result dict.")
                display_msg = (
                    "# Research Complete\n\n"
                    "*Final report not found.*\n\n"
                    + (f"**Agent message:** {error_msg}" if error_msg else "")
                )
            final_ui_update[markdown_display_comp] = gr.update(value=display_msg)

        yield final_ui_update


    except Exception as e:
        logger.error(f"Error during Deep Research Agent execution: {e}", exc_info=True)
        gr.Error(f"Research failed: {e}")
        yield {markdown_display_comp: gr.update(value=f"# Research Failed\n\n**Error:**\n```\n{e}\n```")}

    finally:
        # --- 8. Final UI Reset ---
        webui_manager.dr_current_task = None  # Clear task reference
        webui_manager.dr_task_id = None  # Clear running task ID

        yield {
            start_button_comp: gr.update(value="▶️ Run", interactive=True),
            stop_button_comp: gr.update(interactive=False),
            research_task_comp: gr.update(interactive=True),
            resume_task_id_comp: gr.update(value="", interactive=True),
            parallel_num_comp: gr.update(interactive=True),
            save_dir_comp: gr.update(interactive=True),
            max_tasks_comp: gr.update(interactive=True),
            # Keep download button enabled if file exists
            markdown_download_comp: gr.update() if report_file_path and os.path.exists(report_file_path) else gr.update(
                interactive=False)
        }


async def stop_deep_research(webui_manager: WebuiManager) -> Dict[Component, Any]:
    """Handles the Stop button click. Auto-populates resume task ID for easy restart."""
    logger.info("Stop button clicked for Deep Research.")
    agent = webui_manager.dr_agent
    task = webui_manager.dr_current_task
    task_id = webui_manager.dr_task_id
    base_save_dir = getattr(webui_manager, 'dr_save_dir', './tmp/deep_research')

    stop_button_comp = webui_manager.get_component_by_id("deep_research_agent.stop_button")
    start_button_comp = webui_manager.get_component_by_id("deep_research_agent.start_button")
    resume_task_id_comp = webui_manager.get_component_by_id("deep_research_agent.resume_task_id")
    markdown_display_comp = webui_manager.get_component_by_id("deep_research_agent.markdown_display")
    markdown_download_comp = webui_manager.get_component_by_id("deep_research_agent.markdown_download")

    final_update = {
        stop_button_comp: gr.update(interactive=False, value="⏹️ Stopping..."),
    }

    # Always auto-populate resume task ID so user can easily restart
    if task_id:
        final_update[resume_task_id_comp] = gr.update(value=task_id)

    if agent and task and not task.done():
        logger.info("Signalling DeepResearchAgent to stop.")
        try:
            await agent.stop()
        except Exception as e:
            logger.error(f"Error calling agent.stop(): {e}")

        # Give agent a moment to write final files
        await asyncio.sleep(1.5)

        # Try to show the final report or plan if available after stopping
        report_file_path = None
        plan_file_path = None
        if task_id and base_save_dir:
            task_dir = os.path.join(base_save_dir, str(task_id))
            report_file_path = os.path.join(task_dir, "report.md")
            plan_file_path = os.path.join(task_dir, "research_plan.md")

        if report_file_path and os.path.exists(report_file_path):
            report_content = _read_file_safe(report_file_path)
            if report_content:
                final_update[markdown_display_comp] = gr.update(
                    value=report_content + f"\n\n---\n*Research stopped by user. Resume with task ID: `{task_id}`*")
                # Check for PDF download too
                pdf_path = report_file_path.replace("report.md", "report.pdf")
                if os.path.exists(pdf_path):
                    final_update[markdown_download_comp] = gr.update(
                        value=pdf_path, label="📄 Download PDF Report", interactive=True)
                else:
                    final_update[markdown_download_comp] = gr.update(
                        value=report_file_path, label=f"Report ({task_id}.md)", interactive=True)
            else:
                final_update[markdown_display_comp] = gr.update(
                    value=f"# Research Stopped\n\n*Error reading final report file after stop.*\n\nResume with task ID: `{task_id}`")
        elif plan_file_path and os.path.exists(plan_file_path):
            plan_content = _read_file_safe(plan_file_path)
            if plan_content:
                final_update[markdown_display_comp] = gr.update(
                    value=f"# Research Stopped — Partial Progress\n\nResume with task ID: `{task_id}`\n\n---\n\n{plan_content}")
            else:
                final_update[markdown_display_comp] = gr.update(
                    value=f"# Research Stopped by User\n\nResume with task ID: `{task_id}`")
        else:
            final_update[markdown_display_comp] = gr.update(
                value=f"# Research Stopped by User\n\nResume with task ID: `{task_id}`")

        # Keep start button disabled — run_deep_research finally block will re-enable it
        final_update[start_button_comp] = gr.update(interactive=False)

    else:
        logger.warning("Stop clicked but no active research task found.")
        # Reset UI state just in case
        final_update = {
            start_button_comp: gr.update(interactive=True),
            stop_button_comp: gr.update(interactive=False),
            webui_manager.get_component_by_id("deep_research_agent.research_task"): gr.update(interactive=True),
            webui_manager.get_component_by_id("deep_research_agent.resume_task_id"): gr.update(
                value=task_id or "", interactive=True),
            webui_manager.get_component_by_id("deep_research_agent.max_tasks"): gr.update(interactive=True),
            webui_manager.get_component_by_id("deep_research_agent.max_query"): gr.update(interactive=True),
        }

    return final_update


async def update_mcp_server(mcp_file, webui_manager: WebuiManager):
    """
    Load an MCP JSON file, sanitise it (strip 'comment' and other non-spec
    fields), and store the clean JSON in the config textbox so the agent
    receives a valid config.
    Handles Gradio 5.x file-info dicts as well as plain path strings.
    """
    if hasattr(webui_manager, "dr_agent") and webui_manager.dr_agent:
        logger.warning("⚠️ Close controller because mcp file has changed!")
        await webui_manager.dr_agent.close_mcp_client()

    # Resolve file path from Gradio 5.x dict or plain string
    if isinstance(mcp_file, dict):
        resolved_path = mcp_file.get("path") or mcp_file.get("name")
    elif isinstance(mcp_file, str):
        resolved_path = mcp_file
    else:
        resolved_path = None

    if not resolved_path or not os.path.exists(resolved_path) or not resolved_path.endswith('.json'):
        logger.warning(f"{resolved_path} is not a valid MCP file.")
        return gr.update(value=None), gr.update(visible=False)

    try:
        with open(resolved_path, 'r', encoding='utf-8') as f:
            mcp_server = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error reading MCP config: {e}")
        return gr.update(value=None), gr.update(visible=False)

    # Sanitise: strip comment / non-spec fields so the MCP adapter doesn't reject them
    _ALLOWED = {"command", "args", "env", "transport", "url", "headers"}
    servers = mcp_server.get("mcpServers", mcp_server)
    clean = {}
    for name, cfg in servers.items():
        if isinstance(cfg, dict):
            clean[name] = {k: v for k, v in cfg.items() if k in _ALLOWED}
    mcp_server_clean = {"mcpServers": clean}

    logger.info(f"MCP config loaded: {len(clean)} server(s): {list(clean.keys())}")
    config_json = json.dumps(mcp_server_clean, indent=2)
    return gr.update(value=config_json), gr.update(visible=True, value=f"✅ MCP config loaded: {len(clean)} server(s)")


def create_deep_research_agent_tab(webui_manager: WebuiManager):
    """
    Creates a deep research agent tab
    """
    input_components = set(webui_manager.get_components())
    tab_components = {}

    # ═══════════════════════════════════════════════════════════════════
    # TWO-COLUMN LAYOUT  (optimised for 1280 px laptops)
    # Left  col (scale=4, ~38%) : inputs & controls
    # Right col (scale=6, ~62%) : live log + report + download
    # ═══════════════════════════════════════════════════════════════════
    with gr.Row(equal_height=False):

        # ── LEFT COLUMN ───────────────────────────────────────────────
        with gr.Column(scale=4, min_width=320):

            # MCP config (hidden textarea, driven by file upload)
            with gr.Group():
                with gr.Row():
                    mcp_json_file = gr.File(
                        label="MCP server json (optional)",
                        interactive=True,
                        file_types=[".json"],
                    )
                mcp_server_config = gr.Textbox(
                    label="MCP server", lines=3, interactive=True, visible=False
                )
                mcp_status_display = gr.Markdown(visible=False)

            # Task History accordion
            with gr.Accordion("📂 Task History", open=False):
                gr.Markdown("*Select a past task to resume or view its report.*")
                with gr.Row():
                    task_history_dropdown = gr.Dropdown(
                        label="🗓️ Recent Tasks",
                        choices=_load_task_history(),
                        value=None,
                        interactive=True,
                        scale=4,
                    )
                    refresh_history_btn = gr.Button(
                        "🔄", size="sm", scale=1, min_width=48
                    )
                history_load_btn = gr.Button(
                    "📖 Load Report for Selected Task", size="sm"
                )

            # Main task input
            research_task = gr.Textbox(
                label="🔍 Task / Query",
                lines=4,
                value="Give me a detailed travel plan to Switzerland from June 1st to 10th.",
                interactive=True,
                placeholder="Type a research question or an action task (e.g. 'Book the cheapest flight from Delhi to Goa')",
            )
            
            # File upload for context (PDF, DOCX, TXT, etc.)
            file_upload = gr.File(
                label="📎 Attach Files (optional - PDF, DOCX, TXT, CSV, images)",
                file_count="multiple",
                interactive=True,
                file_types=[".pdf", ".docx", ".doc", ".txt", ".md", ".csv", ".json", 
                           ".xml", ".yaml", ".yml", ".html", ".png", ".jpg", ".jpeg"],
            )
            gr.Markdown(
                "💡 **Tip:** Upload files (resume, documents, data) to provide context for your task. "
                "The agent will read and use the content automatically."
            )

            # ── Settings row ──────────────────────────────────────────
            with gr.Row():
                parallel_num = gr.Number(
                    label="Parallel Agents",
                    value=1,
                    precision=0,
                    interactive=True,
                    minimum=1,
                    maximum=4,
                    scale=1,
                )
                max_tasks = gr.Slider(
                    label="Max Tasks",
                    minimum=1, maximum=12, step=1, value=6,
                    interactive=True,
                    scale=3,
                )
            gr.Markdown(
                " ",
            )
            with gr.Row():
                max_query = gr.Textbox(
                    label="Save Directory",
                    value="./tmp/deep_research",
                    interactive=True,
                    scale=3,
                )
                task_type_display = gr.Textbox(
                    label="Task Type",
                    value="—",
                    interactive=False,
                    scale=2,
                )
            resume_task_id = gr.Textbox(
                label="Resume Task ID (leave blank to start new)",
                value="",
                interactive=True,
            )

            # Run / Stop buttons
            with gr.Row():
                stop_button = gr.Button("⏹️ Stop", variant="stop", scale=2, min_width=100)
                start_button = gr.Button("▶️ Run", variant="primary", scale=3, min_width=140)

        # ── RIGHT COLUMN ──────────────────────────────────────────────
        with gr.Column(scale=6, min_width=400):

            # Live agent log (scrollable, dark-ish via CSS elem_id)
            live_log_comp = gr.Textbox(
                label="🖥️ Live Agent Log",
                lines=12,
                max_lines=12,
                interactive=False,
                autoscroll=True,
                elem_id="live-agent-log",
                placeholder="Agent activity will stream here once the task starts…",
            )

            # Research report (markdown rendered)
            markdown_display = gr.Markdown(label="📋 Research Report / Result")

            # Download button
            markdown_download = gr.File(
                label="📄 Download Report (PDF / MD)",
                interactive=False,
            )

    tab_components.update(
        dict(
            research_task=research_task,
            file_upload=file_upload,
            parallel_num=parallel_num,
            max_query=max_query,
            max_tasks=max_tasks,
            task_type_display=task_type_display,
            start_button=start_button,
            stop_button=stop_button,
            markdown_display=markdown_display,
            markdown_download=markdown_download,
            resume_task_id=resume_task_id,
            mcp_json_file=mcp_json_file,
            mcp_server_config=mcp_server_config,
            task_history_dropdown=task_history_dropdown,
            live_log=live_log_comp,
        )
    )
    webui_manager.add_components("deep_research_agent", tab_components)
    webui_manager.init_deep_research_agent()

    async def update_wrapper(mcp_file):
        """Wrapper for handle_pause_resume."""
        update_dict = await update_mcp_server(mcp_file, webui_manager)
        yield update_dict

    mcp_json_file.change(
        update_wrapper,
        inputs=[mcp_json_file],
        outputs=[mcp_server_config, mcp_status_display]
    )

    dr_tab_outputs = list(tab_components.values())
    all_managed_inputs = set(webui_manager.get_components())

    # --- Define Event Handler Wrappers ---
    async def start_wrapper(comps: Dict[Component, Any]) -> AsyncGenerator[Dict[Component, Any], None]:
        async for update in run_deep_research(webui_manager, comps):
            yield update

    async def stop_wrapper() -> AsyncGenerator[Dict[Component, Any], None]:
        update_dict = await stop_deep_research(webui_manager)
        yield update_dict

    # --- Connect Handlers ---
    start_button.click(
        fn=start_wrapper,
        inputs=all_managed_inputs,
        outputs=dr_tab_outputs
    )

    stop_button.click(
        fn=stop_wrapper,
        inputs=None,
        outputs=dr_tab_outputs
    )

    # \u2500\u2500 Task History event handlers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def _refresh_history_handler():
        return gr.update(choices=_load_task_history())

    def _history_select_handler(selected_task_id):
        """Auto-fill the Resume Task ID field when a history entry is selected."""
        if not selected_task_id:
            return ""
        return selected_task_id

    def _load_history_report_handler(selected_task_id):
        """Load the report for the selected task into the display and download button."""
        if not selected_task_id:
            return gr.update(value="\u2757 Please select a task from the history dropdown."), gr.update(interactive=False)
        base_dir = "./tmp/deep_research"
        task_dir = os.path.join(base_dir, str(selected_task_id))
        md_path = os.path.join(task_dir, "report.md")
        pdf_path = os.path.join(task_dir, "report.pdf")
        report_content = _read_file_safe(md_path)
        if report_content:
            download_path = pdf_path if os.path.exists(pdf_path) else md_path
            label = "\ud83d\udcc4 Download PDF Report" if os.path.exists(pdf_path) else f"Report ({selected_task_id[:12]}.md)"
            return (
                gr.update(value=report_content),
                gr.update(value=download_path, label=label, interactive=True),
            )
        plan_content = _read_file_safe(os.path.join(task_dir, "research_plan.md"))
        if plan_content:
            return gr.update(value=f"# Research in Progress\n\n{plan_content}"), gr.update(interactive=False)
        return gr.update(value="No report found for this task ID."), gr.update(interactive=False)

    refresh_history_btn.click(
        fn=_refresh_history_handler,
        inputs=None,
        outputs=[task_history_dropdown],
    )
    task_history_dropdown.change(
        fn=_history_select_handler,
        inputs=[task_history_dropdown],
        outputs=[resume_task_id],
    )
    history_load_btn.click(
        fn=_load_history_report_handler,
        inputs=[task_history_dropdown],
        outputs=[markdown_display, markdown_download],
    )
