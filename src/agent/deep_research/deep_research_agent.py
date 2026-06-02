import asyncio
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from browser_use.browser.browser import BrowserConfig

# Langchain imports
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import StructuredTool, Tool

# Langgraph imports
from langgraph.graph import StateGraph
from pydantic import BaseModel, Field

from browser_use.browser.context import BrowserContextConfig

from src.agent.browser_use.browser_use_agent import BrowserUseAgent
from src.browser.custom_browser import CustomBrowser
from src.controller.custom_controller import CustomController
from src.utils.mcp_client import setup_mcp_client_and_tools

logger = logging.getLogger(__name__)

# Global UI log queue for live updates (set by the UI tab)
_UI_LOG_QUEUE = None

def _push_to_ui_log(message: str):
    """Push a status message directly to the UI live log."""
    global _UI_LOG_QUEUE
    if _UI_LOG_QUEUE is not None:
        try:
            from datetime import datetime
            ts = datetime.now().strftime("%H:%M:%S")
            _UI_LOG_QUEUE.append(f"[{ts}] {message}")
        except Exception:
            pass

# Constants
REPORT_FILENAME = "report.md"
PLAN_FILENAME = "research_plan.md"
SEARCH_INFO_FILENAME = "search_info.json"

# Thread-safe module-level state with locks
_STATE_LOCK = threading.Lock()
_AGENT_STOP_FLAGS: Dict[str, threading.Event] = {}
_BROWSER_AGENT_INSTANCES: Dict[str, Any] = {}
_TASK_TYPE_REGISTRY: Dict[str, str] = {}  # task_id → "action" | "research"

BROWSER_TASK_TIMEOUT = 600  # 10-minute hard timeout per individual browser task (increased for complex actions)


async def run_single_browser_task(
        task_query: str,
        task_id: str,
        llm: Any,  # Pass the main LLM
        browser_config: Dict[str, Any],
        stop_event: threading.Event,
        use_vision: bool = False,
        mcp_server_config: Optional[Dict[str, Any]] = None,
        global_task_type: Optional[str] = None,  # LLM-classified type from planning_node
) -> Dict[str, Any]:
    """
    Runs a single BrowserUseAgent task.
    Manages browser creation and closing for this specific task.
    """
    if not BrowserUseAgent:
        return {
            "query": task_query,
            "error": "BrowserUseAgent components not available.",
        }

    # --- Browser Setup ---
    # These should ideally come from the main agent's config
    headless = browser_config.get("headless", False)
    window_w = browser_config.get("window_width", 1280)
    window_h = browser_config.get("window_height", 1100)
    browser_user_data_dir = browser_config.get("user_data_dir", None)
    use_own_browser = browser_config.get("use_own_browser", False)
    browser_binary_path = browser_config.get("browser_binary_path", None)
    wss_url = browser_config.get("wss_url", None)
    cdp_url = browser_config.get("cdp_url", None)
    disable_security = browser_config.get("disable_security", False)
    # A4: inherit stealth and extra args from browser settings UI
    extra_browser_args_base = list(browser_config.get("extra_browser_args", []) or [])
    stealth_mode = browser_config.get("stealth_mode", True)

    bu_browser = None
    bu_browser_context = None
    try:
        logger.info(f"Starting browser task for query: {task_query}")
        extra_args = list(extra_browser_args_base)  # start from inherited args
        if not stealth_mode:
            # Add sentinel only if stealth is OFF and not already there
            if "--no-stealth" not in extra_args:
                extra_args.append("--no-stealth")
        if use_own_browser:
            browser_binary_path = os.getenv("BROWSER_PATH", None) or browser_binary_path
            if browser_binary_path == "":
                browser_binary_path = None
            browser_user_data = browser_user_data_dir or os.getenv("BROWSER_USER_DATA", None)
            if browser_user_data:
                extra_args += [f"--user-data-dir={browser_user_data}"]
        else:
            browser_binary_path = None

        bu_browser = CustomBrowser(
            config=BrowserConfig(
                headless=headless,
                browser_binary_path=browser_binary_path,
                extra_browser_args=extra_args,
                wss_url=wss_url,
                cdp_url=cdp_url,
                new_context_config=BrowserContextConfig(
                    window_width=window_w,
                    window_height=window_h,
                )
            )
        )

        context_config = BrowserContextConfig(
            save_downloads_path="./tmp/downloads",
            window_height=window_h,
            window_width=window_w,
            force_new_context=True,
        )
        bu_browser_context = await bu_browser.new_context(config=context_config)

        # Controller setup — with MCP tools if available
        bu_controller = CustomController()
        if mcp_server_config:
            try:
                logger.info("Setting up MCP tools for browser sub-agent...")
                await bu_controller.setup_mcp_client(mcp_server_config)
                _mcp_tools_count = bu_controller.mcp_total_tools
                _mcp_servers = bu_controller.mcp_connected_servers
                logger.info(f"MCP tools loaded for sub-agent: {_mcp_tools_count} tools from {_mcp_servers}")
            except Exception as mcp_err:
                logger.warning(f"Failed to setup MCP for sub-agent: {mcp_err}")
                _mcp_tools_count = 0
                _mcp_servers = []
        else:
            _mcp_tools_count = 0
            _mcp_servers = []

        # ── Detect task mode: file_creation vs. action vs. research ─────────
        _file_keywords = (
            "create", "write", "make", "build", "generate", "save",
            "develop", "setup", "set up", "scaffold", "code",
        )
        _file_target_keywords = (
            "file", "folder", "directory", "html", "css", "javascript", "js",
            "code", "script", "page", "desktop", "document", "project",
            "app", "application", "website", "site", "program", "module",
            "component", "template", "form", "readme", "json", "xml",
            "python", "java", "react", "vue", "angular", "typescript", "ts",
        )
        _action_keywords = (
            "book", "buy", "purchase", "order", "reserve", "register",
            "sign up", "signup", "apply", "submit", "fill", "download",
            "login", "log in", "checkout", "pay", "ticket",
            "appointment", "schedule", "enroll", "subscribe",
        )
        # Words that look like action keywords but appear in research queries
        _research_modifiers = (
            "compare", "comparison", "review", "reviews", "best", "top",
            "cheapest", "analysis", "analyze", "analyse", "research",
            "history", "about", "learn", "what is", "how does", "explain",
            "guide", "tutorial", "list of", "statistics", "trends",
        )
        _query_lower = task_query.lower()

        # Check if this is a file creation/writing task (should use MCP filesystem tools)
        _is_file_task = (
            any(kw in _query_lower for kw in _file_keywords) and
            any(kw in _query_lower for kw in _file_target_keywords)
        )

        # Trust the global LLM classification if provided.
        # Only use keyword fallback when global_task_type was not set.
        _has_research_modifier = any(kw in _query_lower for kw in _research_modifiers)
        if global_task_type:
            _is_action = global_task_type.lower() == "action" and not _has_research_modifier
        else:
            _is_action = (
                any(kw in _query_lower for kw in _action_keywords)
                and not _has_research_modifier
            )

        if _is_file_task and _mcp_tools_count > 0:
            # ── FILE CREATION MODE — use MCP filesystem tools directly ──
            logger.info(f"FILE CREATION mode detected. MCP tools available: {_mcp_tools_count}")

            # Build a list of available MCP tool names for the prompt
            _mcp_tool_names = [
                name for name in bu_controller.registry.registry.actions.keys()
                if name.startswith("mcp.")
            ]
            _tool_list_str = "\n".join(f"  - {t}" for t in _mcp_tool_names)

            # Detect OS for correct path format
            import platform
            _os_type = platform.system()
            _is_windows = _os_type == "Windows"
            _desktop_path = os.path.join(os.path.expanduser("~"), "Desktop") if _is_windows else "~/Desktop"
            _path_example = "C:\\Users\\YourName\\Desktop\\FolderName\\file.txt" if _is_windows else "/home/user/Desktop/FolderName/file.txt"
            _path_format = "Windows paths with backslashes (C:\\\\Users\\\\...)" if _is_windows else "Unix paths with forward slashes (/home/...)"
            
            bu_task_prompt = f"""You are a file creation agent with access to MCP filesystem tools. Your job is to CREATE files and folders directly on the user's computer using the MCP tools available to you.

TASK: {task_query}

SYSTEM INFO:
- Operating System: {_os_type}
- Desktop Path: {_desktop_path}
- Path Format: {_path_format}
- Example Full Path: {_path_example}

AVAILABLE MCP TOOLS:
{_tool_list_str}

CRITICAL INSTRUCTIONS:
1. Do NOT open a web browser or search the web. You have all the knowledge you need to create these files.
2. Use the MCP filesystem tools to:
   a. First, create the folder/directory if needed (use the appropriate write/create tool)
   b. Then, write each file with complete, production-quality code
3. For web development tasks (HTML/CSS/JavaScript):
   - Write clean, semantic HTML5 with proper DOCTYPE, head, and body sections
   - Write modern, responsive CSS with good styling (use flexbox/grid, nice colors, proper spacing)
   - Write JavaScript with proper event listeners, validation, and user feedback
   - Link the CSS and JS files properly in the HTML
4. For Python/Java/other programming tasks:
   - Write clean, well-structured code following best practices
   - Include proper imports, error handling, and comments
   - Create any supporting files (requirements.txt, README.md, etc.) if appropriate
5. Make ALL code COMPLETE and FUNCTIONAL — not placeholder or skeleton code.
6. When reporting completion:
   - Use ACTUAL PATHS from your current OS ({_os_type})
   - If user requested "desktop folder", use: {_desktop_path}
   - Report full absolute paths in {_path_format} format
   - Example: {_path_example}

IMPORTANT: Use the MCP tools (listed above) to write files. Do NOT try to open VS Code, File Explorer, or any other application. The MCP filesystem tools can create directories and write files directly. Report paths in the CORRECT format for {_os_type} system!
"""
            _max_steps = 25  # File creation tasks need moderate steps

        elif _is_action:
            # ---- Extract structured constraints from the query ----
            import re as _cre
            _price_m = _cre.search(r'(?:under|below|less than|within|max|maximum|upto|up to)\s*(?:rs\.?|inr|\u20b9)?\s*(\d[\.\d,]*)\s*(?:rs\.?|inr|\u20b9|rupee)?', _query_lower)
            _price_cap = _price_m.group(1).replace(',','') if _price_m else None
            _date_m = _cre.search(r'(\d{1,2}\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:\s*\d{2,4})?|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|tomorrow|today)', _query_lower)
            _travel_date = _date_m.group(1) if _date_m else None
            _from_m = _cre.search(r'from\s+([a-z ]+?)\s+to\s+', _query_lower)
            _to_m   = _cre.search(r'\bto\s+([a-z]+)', _query_lower)
            _origin = _from_m.group(1).strip().title() if _from_m else None
            _dest   = _to_m.group(1).strip().title() if _to_m else None

            _constraint_block = "\n"
            if _origin and _dest:
                _constraint_block += f"ROUTE: {_origin} to {_dest}\n"
            if _travel_date:
                _constraint_block += f"DATE: {_travel_date}\n"
            if _price_cap:
                _constraint_block += f"PRICE CAP: under Rs.{_price_cap} (REJECT any result above this)\n"

            # Detect if this is a travel/booking task to provide specialized sites
            _is_travel = any(kw in _query_lower for kw in ("flight", "train", "hotel", "bus", "travel"))
            _is_shopping = any(kw in _query_lower for kw in ("buy", "purchase", "order", "shop"))
            _is_food = any(kw in _query_lower for kw in ("food", "restaurant", "delivery", "swiggy", "zomato"))

            _site_strategy = ""
            if _is_travel:
                _site_strategy = """
MULTI-SITE STRATEGY (follow in order until you find a result meeting ALL constraints):
Site 1 → https://www.makemytrip.com
Site 2 → https://www.cleartrip.com
Site 3 → https://www.irctc.co.in (for trains)
Site 4 → https://www.paytm.com/flights
"""
            elif _is_shopping:
                _site_strategy = """
MULTI-SITE STRATEGY (compare across these sites):
Site 1 → https://www.amazon.in
Site 2 → https://www.flipkart.com
Site 3 → https://www.myntra.com (for fashion)
"""
            elif _is_food:
                _site_strategy = """
MULTI-SITE STRATEGY (check these food delivery sites):
Site 1 → https://www.swiggy.com
Site 2 → https://www.zomato.com
"""

            # ACTION MODE — agent must interact and complete the task
            bu_task_prompt = f"""You are an advanced browser automation agent with PERSISTENCE and ADAPTABILITY. Your mission is to COMPLETE the task below using REAL browser actions. You have access to powerful new tools for handling complex forms, date pickers, and data extraction.

TASK: {task_query}
{_constraint_block}
{_site_strategy}

🛠️ NEW ADVANCED ACTIONS AVAILABLE:
- fill_autocomplete_field: For airport/city dropdowns that require typing + selection
- interact_with_date_picker: For calendar widgets
- extract_table_data: Scrape structured data from HTML tables
- extract_prices_from_page: Find all prices on the current page
- smart_wait_for_element: Wait for dynamic content to load
- fill_form_intelligently: Auto-detect and fill forms

📋 STEP-BY-STEP EXECUTION PLAN:
1. Navigate to target website using go_to_url (NEVER use search_google — it triggers reCAPTCHA)
2. Wait for page to load fully (use smart_wait_for_element if needed)
3. Identify and fill search/booking forms:
   - For airport/city fields: use fill_autocomplete_field
   - For date fields: use interact_with_date_picker
   - For text fields: use input_text
   - For dropdowns: use select_dropdown_option
4. Submit form and wait for results (use smart_wait_for_element to wait for result container)
5. Extract data:
   - For price tables: use extract_table_data
   - For scattered prices: use extract_prices_from_page
   - For general content: use extract_page_content
6. If Site 1 fails, IMMEDIATELY try Site 2, then Site 3 — DO NOT GIVE UP after one attempt

🚫 FAILURE HANDLING (CRITICAL):
- If an element is not found: Try alternative selectors, scroll, or use coordinates
- If a form field won't fill: Try clicking it first, clearing it, then typing
- If autocomplete doesn't work: Press Enter/Tab after typing
- If date picker fails: Try typing the date directly in the input field
- If results don't load: Wait 5-10 seconds and check again
- If a site is broken/slow: Move to the next site in the strategy list
- ONLY report "unable to complete" if ALL sites have been tried AND all actions have failed 3+ times

✅ SUCCESS CRITERIA — Your final answer MUST include:
1. ✓ Site(s) visited (e.g., "Tried makemytrip.com, then cleartrip.com")
2. ✓ Concrete results (e.g., "Found 8 flights, cheapest is ₹3,450 on IndiGo")
3. ✓ Specific details (airline/operator, time, price, booking URL)
4. ✓ Next steps for user (e.g., "Click 'Book Now' on cleartrip.com to proceed")
5. ✓ Screenshot evidence if possible

⚠️ NEVER SAY "unable to complete" without trying:
- At least 2-3 different websites
- At least 3 retry attempts per failed action
- Alternative methods (coordinates, keyboard shortcuts, etc.)

🎯 REMEMBER: You are like a human assistant — persistent, creative, and committed to completing the task successfully!
"""
            _max_steps = 50  # Increased from 35 to allow multi-site retries
        else:
            # RESEARCH MODE — gather and summarise information
            bu_task_prompt = f"""You are a web research agent. Your job is to find accurate, detailed information for the following research query.

RESEARCH QUERY: {task_query}

INSTRUCTIONS:
- Do NOT use search_google (reCAPTCHA risk). Instead:
  • Use DuckDuckGo: navigate to https://duckduckgo.com and search there.
  • Or use Bing: navigate to https://www.bing.com and search there.
  • Or navigate directly to the target website if you know its URL.
- Visit the most relevant search results (at least 2-3 sources).
- Extract specific data, numbers, facts and quotes — not vague generalities.
- If a page is behind a paywall or login, try a different source.

OUTPUT REQUIREMENTS — for each piece of information found, provide:
1. The specific data/fact/number found.
2. The source page title.
3. The full URL of the source.

Be thorough and precise. Prioritise recent, authoritative sources.
"""
            _max_steps = 20  # Research tasks

        bu_agent_instance = BrowserUseAgent(
            task=bu_task_prompt,
            llm=llm,
            browser=bu_browser,
            browser_context=bu_browser_context,
            controller=bu_controller,
            use_vision=use_vision,
            source="webui",
        )

        # Store instance for potential stop() call
        task_key = f"{task_id}_{uuid.uuid4()}"
        with _STATE_LOCK:
            _BROWSER_AGENT_INSTANCES[task_key] = bu_agent_instance

        if stop_event.is_set():
            logger.info(f"Browser task for '{task_query}' cancelled before start.")
            return {"query": task_query, "result": None, "status": "cancelled"}

        _mode_label = "FILE" if (_is_file_task and _mcp_tools_count > 0) else ("ACTION" if _is_action else "RESEARCH")
        logger.info(f"Running BrowserUseAgent ({_mode_label} mode) for: {task_query}")

        # ── Stop-signal monitor: propagates stop_event into the running agent ──
        _stop_monitor_task = None

        async def _monitor_stop():
            """Poll stop_event and stop the browser agent if signalled."""
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(0.5)
                # stop_event was set — signal the agent
                logger.info(f"Stop signal detected, stopping browser agent for: {task_query[:60]}")
                try:
                    # Guard: only stop if the agent instance is still valid and running
                    if bu_agent_instance and hasattr(bu_agent_instance, 'state') and not getattr(bu_agent_instance.state, 'stopped', True):
                        bu_agent_instance.state.stopped = True
                except Exception:
                    pass
            except asyncio.CancelledError:
                pass

        _stop_monitor_task = asyncio.create_task(_monitor_stop())

        try:
            result = await asyncio.wait_for(
                bu_agent_instance.run(max_steps=_max_steps),
                timeout=BROWSER_TASK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Browser task TIMED OUT ({BROWSER_TASK_TIMEOUT}s): {task_query[:80]}")
            try:
                await bu_agent_instance.stop()
            except Exception:
                pass
            return {"query": task_query, "error": f"Timed out after {BROWSER_TASK_TIMEOUT}s", "status": "failed"}
        finally:
            if _stop_monitor_task and not _stop_monitor_task.done():
                _stop_monitor_task.cancel()

        logger.info(f"BrowserUseAgent finished for: {task_query}")

        final_data = result.final_result()

        if stop_event.is_set():
            logger.info(f"Browser task for '{task_query}' stopped during execution.")
            return {"query": task_query, "result": final_data, "status": "stopped"}
        else:
            logger.info(f"Browser result for '{task_query}': {final_data}")
            return {"query": task_query, "result": final_data, "status": "completed"}

    except Exception as e:
        logger.error(
            f"Error during browser task for query '{task_query}': {e}", exc_info=True
        )
        return {"query": task_query, "error": str(e), "status": "failed"}
    finally:
        # Clean up MCP client if it was set up
        if bu_controller and bu_controller.mcp_client:
            try:
                await bu_controller.close_mcp_client()
                logger.info("Closed MCP client for sub-agent.")
            except Exception as e:
                logger.error(f"Error closing MCP client: {e}")
        if bu_browser_context:
            try:
                await bu_browser_context.close()
                bu_browser_context = None
                logger.info("Closed browser context.")
            except Exception as e:
                logger.error(f"Error closing browser context: {e}")
        if bu_browser:
            try:
                await bu_browser.close()
                bu_browser = None
                logger.info("Closed browser.")
            except Exception as e:
                logger.error(f"Error closing browser: {e}")

        if task_key in _BROWSER_AGENT_INSTANCES:
            with _STATE_LOCK:
                _BROWSER_AGENT_INSTANCES.pop(task_key, None)


class BrowserSearchInput(BaseModel):
    queries: List[str] = Field(
        description="List of distinct search queries to find information relevant to the research task."
    )


async def _run_browser_search_tool(
        queries: List[str],
        task_id: str,  # Injected dependency
        llm: Any,  # Injected dependency
        browser_config: Dict[str, Any],
        stop_event: threading.Event,
        max_parallel_browsers: int = 1,
        mcp_server_config: Optional[Dict[str, Any]] = None,
        global_task_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Internal function to execute parallel browser searches based on LLM-provided queries.
    Handles concurrency and stop signals.
    """
    # Resolve task type from registry if not explicitly passed
    if not global_task_type:
        with _STATE_LOCK:
            global_task_type = _TASK_TYPE_REGISTRY.get(task_id)

    # Process all queries in batches of max_parallel_browsers instead of truncating
    logger.info(
        f"[Browser Tool {task_id}] Running search for {len(queries)} queries "
        f"(max parallel: {max_parallel_browsers}): {queries}"
    )

    results = []
    semaphore = asyncio.Semaphore(max_parallel_browsers)

    async def task_wrapper(query):
        async with semaphore:
            if stop_event.is_set():
                logger.info(
                    f"[Browser Tool {task_id}] Skipping task due to stop signal: {query}"
                )
                return {"query": query, "result": None, "status": "cancelled"}
            # Pass necessary injected configs and the stop event
            return await run_single_browser_task(
                query,
                task_id,
                llm,
                browser_config,
                stop_event,
                mcp_server_config=mcp_server_config,
                global_task_type=global_task_type,
            )

    # Process queries in batches of max_parallel_browsers
    # This ensures ALL queries are executed, not silently truncated
    STAGGER_DELAY = 2  # seconds between each browser task start

    all_results = []
    for batch_start in range(0, len(queries), max_parallel_browsers):
        if stop_event.is_set():
            logger.info(f"[Browser Tool {task_id}] Stop signal — skipping remaining batches.")
            break
        batch = queries[batch_start:batch_start + max_parallel_browsers]
        logger.info(f"[Browser Tool {task_id}] Processing batch {batch_start // max_parallel_browsers + 1}: {batch}")

        async def staggered_task_wrapper(query, index):
            """Wraps task_wrapper with a staggered start delay."""
            if index > 0:
                await asyncio.sleep(index * STAGGER_DELAY)
            return await task_wrapper(query)

        tasks = [staggered_task_wrapper(query, i) for i, query in enumerate(batch)]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, res in enumerate(batch_results):
            query = batch[i]
            if isinstance(res, Exception):
                logger.error(
                    f"[Browser Tool {task_id}] Gather caught exception for query '{query}': {res}",
                    exc_info=True,
                )
                all_results.append(
                    {"query": query, "error": str(res), "status": "failed"}
                )
            elif isinstance(res, dict):
                all_results.append(res)
            else:
                logger.error(
                    f"[Browser Tool {task_id}] Unexpected result type for query '{query}': {type(res)}"
                )
                all_results.append(
                    {"query": query, "error": "Unexpected result type", "status": "failed"}
                )

    logger.info(
        f"[Browser Tool {task_id}] Finished search. Results count: {len(all_results)}"
    )
    return all_results


def create_browser_search_tool(
        llm: Any,
        browser_config: Dict[str, Any],
        task_id: str,
        stop_event: threading.Event,
        max_parallel_browsers: int = 1,
        mcp_server_config: Optional[Dict[str, Any]] = None,
        global_task_type: Optional[str] = None,
) -> StructuredTool:
    """Factory function to create the browser search tool with necessary dependencies."""
    # Use partial to bind the dependencies that aren't part of the LLM call arguments
    from functools import partial

    bound_tool_func = partial(
        _run_browser_search_tool,
        task_id=task_id,
        llm=llm,
        browser_config=browser_config,
        stop_event=stop_event,
        max_parallel_browsers=max_parallel_browsers,
        mcp_server_config=mcp_server_config,
        global_task_type=global_task_type,
    )

    return StructuredTool.from_function(
        coroutine=bound_tool_func,
        name="parallel_browser_search",
        description=f"""Use this tool to actively search the web for information related to a specific research task or question.
It runs up to {max_parallel_browsers} searches in parallel using a browser agent for better results than simple scraping.
Provide a list of distinct search queries(up to {max_parallel_browsers}) that are likely to yield relevant information.""",
        args_schema=BrowserSearchInput,
    )


# --- Langgraph State Definition ---


class ResearchTaskItem(TypedDict):
    # step: int # Maybe step within category, or just implicit by order
    task_description: str
    status: str  # "pending", "completed", "failed"
    queries: Optional[List[str]]
    result_summary: Optional[str]


class ResearchCategoryItem(TypedDict):
    category_name: str
    tasks: List[ResearchTaskItem]
    # Optional: category_status: str # Could be "pending", "in_progress", "completed"


class DeepResearchState(TypedDict):
    task_id: str
    topic: str
    research_plan: List[ResearchCategoryItem]
    search_results: List[Dict[str, Any]]
    llm: Any
    tools: List[Tool]
    output_dir: Path
    browser_config: Dict[str, Any]
    final_report: Optional[str]
    current_category_index: int
    current_task_index_in_category: int
    stop_requested: bool
    error_message: Optional[str]
    messages: List[BaseMessage]
    # B5: total task limit from UI slider (default 12 = 4 cats × 3 tasks)
    max_tasks_total: int
    # A1: detected task type ("research" | "action")
    task_type: str
    # MCP server configuration to pass to browser sub-agents
    mcp_server_config: Optional[Dict[str, Any]]


# --- Langgraph Nodes ---


def _load_previous_state(task_id: str, output_dir: str) -> Dict[str, Any]:
    state_updates = {}
    plan_file = os.path.join(output_dir, PLAN_FILENAME)
    search_file = os.path.join(output_dir, SEARCH_INFO_FILENAME)

    loaded_plan: List[ResearchCategoryItem] = []
    next_cat_idx, next_task_idx = 0, 0
    found_pending = False

    if os.path.exists(plan_file):
        try:
            with open(plan_file, "r", encoding="utf-8") as f:
                current_category: Optional[ResearchCategoryItem] = None
                lines = f.readlines()
                cat_counter = 0
                task_counter_in_cat = 0

                for line_num, line_content in enumerate(lines):
                    line = line_content.strip()
                    if line.startswith("## "):  # Category
                        if current_category:  # Save previous category
                            loaded_plan.append(current_category)
                            if not found_pending:  # If previous category was all done, advance cat counter
                                cat_counter += 1
                                task_counter_in_cat = 0
                        category_name = line[line.find(" "):].strip()  # Get text after "## X. "
                        current_category = ResearchCategoryItem(category_name=category_name, tasks=[])
                    elif (line.startswith("- [ ]") or line.startswith("- [x]") or line.startswith(
                            "- [-]")) and current_category:  # Task
                        status = "pending"
                        if line.startswith("- [x]"):
                            status = "completed"
                        elif line.startswith("- [-]"):
                            status = "failed"

                        task_desc = line[5:].strip()
                        current_category["tasks"].append(
                            ResearchTaskItem(task_description=task_desc, status=status, queries=None,
                                             result_summary=None)
                        )
                        if status == "pending" and not found_pending:
                            next_cat_idx = cat_counter
                            next_task_idx = task_counter_in_cat
                            found_pending = True
                        if not found_pending:  # only increment if previous tasks were completed/failed
                            task_counter_in_cat += 1

                if current_category:  # Append last category
                    loaded_plan.append(current_category)

            if loaded_plan:
                state_updates["research_plan"] = loaded_plan
                if not found_pending and loaded_plan:  # All tasks were completed or failed
                    next_cat_idx = len(loaded_plan)  # Points beyond the last category
                    next_task_idx = 0
                state_updates["current_category_index"] = next_cat_idx
                state_updates["current_task_index_in_category"] = next_task_idx
                logger.info(
                    f"Loaded hierarchical research plan from {plan_file}. "
                    f"Next task: Category {next_cat_idx}, Task {next_task_idx} in category."
                )
            else:
                logger.warning(f"Plan file {plan_file} was empty or malformed.")

        except Exception as e:
            logger.error(f"Failed to load or parse research plan {plan_file}: {e}", exc_info=True)
            state_updates["error_message"] = f"Failed to load research plan: {e}"
    else:
        logger.info(f"Plan file {plan_file} not found. Will start fresh.")

    if os.path.exists(search_file):
        try:
            with open(search_file, "r", encoding="utf-8") as f:
                state_updates["search_results"] = json.load(f)
                logger.info(f"Loaded search results from {search_file}")
        except Exception as e:
            logger.error(f"Failed to load search results {search_file}: {e}")
            state_updates["error_message"] = (
                    state_updates.get("error_message", "") + f" Failed to load search results: {e}").strip()

    return state_updates


def _save_plan_to_md(plan: List[ResearchCategoryItem], output_dir: str):
    plan_file = os.path.join(output_dir, PLAN_FILENAME)
    try:
        with open(plan_file, "w", encoding="utf-8") as f:
            f.write(f"# Research Plan\n\n")
            for cat_idx, category in enumerate(plan):
                f.write(f"## {cat_idx + 1}. {category['category_name']}\n\n")
                for task_idx, task in enumerate(category['tasks']):
                    marker = "- [x]" if task["status"] == "completed" else "- [ ]" if task[
                                                                                          "status"] == "pending" else "- [-]"  # [-] for failed
                    f.write(f"  {marker} {task['task_description']}\n")
                f.write("\n")
        logger.info(f"Hierarchical research plan saved to {plan_file}")
    except Exception as e:
        logger.error(f"Failed to save research plan to {plan_file}: {e}")


def _save_search_results_to_json(results: List[Dict[str, Any]], output_dir: str):
    """Appends or overwrites search results to a JSON file."""
    search_file = os.path.join(output_dir, SEARCH_INFO_FILENAME)
    try:
        # Simple overwrite for now, could be append
        with open(search_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info(f"Search results saved to {search_file}")
    except Exception as e:
        logger.error(f"Failed to save search results to {search_file}: {e}")


def _save_report_to_md(report: str, output_dir: Path):
    """Saves the final report to a markdown file."""
    report_file = os.path.join(output_dir, REPORT_FILENAME)
    try:
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info(f"Final report saved to {report_file}")
    except Exception as e:
        logger.error(f"Failed to save final report to {report_file}: {e}")


def _save_report_to_pdf(report: str, output_dir, topic: str = "Research Report", plan: Optional[List[ResearchCategoryItem]] = None) -> Optional[str]:
    """Saves the final report as a Unicode-safe PDF using Arial TTF when available.
    
    Args:
        report: Markdown report content
        output_dir: Output directory path
        topic: Report title
        plan: Optional research plan with task status (for detailed reports)
    """
    import re as _re
    import unicodedata as _ud
    from datetime import datetime as _dt

    pdf_path = os.path.join(str(output_dir), "report.pdf")

    def _sanitize(text: str) -> str:
        """NFKD-normalise then drop anything that can't survive latin-1."""
        return _ud.normalize('NFKD', text).encode('latin-1', errors='ignore').decode('latin-1')

    def _clean_inline(text: str) -> str:
        text = _re.sub(r'\*\*(.+?)\*\*', lambda m: m.group(1).upper(), text)
        text = _re.sub(r'\*(.+?)\*', r'\1', text)
        text = _re.sub(r'`(.+?)`', r'\1', text)
        text = _re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        text = _re.sub(r'^>+\s*', '', text)
        return text.strip()

    try:
        from fpdf import FPDF

        # ---- Try to register Arial (full Unicode) from Windows fonts ----
        _ARIAL_REGULAR = r"C:\Windows\Fonts\arial.ttf"
        _ARIAL_BOLD    = r"C:\Windows\Fonts\arialbd.ttf"
        _ARIAL_ITALIC  = r"C:\Windows\Fonts\ariali.ttf"
        _has_arial = os.path.exists(_ARIAL_REGULAR)
        _FONT = "Arial" if _has_arial else "Helvetica"

        class _ReportPDF(FPDF):
            def header(self):
                pass
            def footer(self):
                self.set_y(-13)
                stamp = f"Generated by CoAgents  -  {_dt.now().strftime('%Y-%m-%d')}"
                self.set_font(_FONT, "I", 8)
                self.set_text_color(150, 150, 150)
                self.cell(0, 8, stamp if _has_arial else _sanitize(stamp), align="C")

        pdf = _ReportPDF()

        if _has_arial:
            # fpdf2 >= 2.5.1 auto-detects Unicode support; 'uni' parameter is deprecated
            pdf.add_font("Arial", "",  _ARIAL_REGULAR)
            pdf.add_font("Arial", "B", _ARIAL_BOLD)
            pdf.add_font("Arial", "I", _ARIAL_ITALIC)
            logger.info("[PDF] Using Arial TTF (full Unicode).")
        else:
            logger.warning("[PDF] Arial not found; using Helvetica — special chars stripped.")

        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.set_margins(18, 18, 18)
        pdf.add_page()

        # ── Title block ──────────────────────────────────────────────────
        _raw_topic = (topic or "Research Report")[:120]
        _title = _raw_topic if _has_arial else _sanitize(_raw_topic)
        pdf.set_font(_FONT, "B", 18)
        pdf.set_text_color(25, 90, 190)
        pdf.multi_cell(0, 12, _title, align="C")
        pdf.ln(2)
        pdf.set_font(_FONT, "", 9)
        pdf.set_text_color(120, 120, 120)
        _stamp = _dt.now().strftime("Generated: %B %d, %Y  %H:%M")
        pdf.cell(0, 7, _stamp if _has_arial else _sanitize(_stamp),
                 align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.set_draw_color(25, 90, 190)
        pdf.set_line_width(0.4)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(6)

        # Define _cell helper function BEFORE using it
        def _cell(text: str):
            """Write one multi_cell line; one bad line never aborts the whole PDF."""
            t = text if _has_arial else _sanitize(text)
            try:
                pdf.multi_cell(0, 7, t)
            except Exception:
                try:
                    pdf.multi_cell(0, 7, _sanitize(t))
                except Exception:
                    pass

        # ── Research Plan (if provided) ──────────────────────────────────
        if plan:
            pdf.set_font(_FONT, "B", 14)
            pdf.set_text_color(25, 90, 190)
            _cell("Research Plan & Task Status")
            pdf.ln(3)
            pdf.set_font(_FONT, "", 9)
            pdf.set_text_color(80, 80, 80)
            for cat_idx, category in enumerate(plan):
                pdf.set_font(_FONT, "B", 10)
                pdf.set_text_color(40, 40, 120)
                _cell(f"{cat_idx + 1}. {category.get('category_name', 'Category')}")
                pdf.set_font(_FONT, "", 9)
                pdf.set_text_color(60, 60, 60)
                for task in category.get('tasks', []):
                    task_status = task.get('status', 'pending')
                    marker = "[x]" if task_status == "completed" else "[ ]" if task_status == "pending" else "[-]"
                    task_desc = task.get('task_description', 'Task')
                    status_symbol = "✓" if task_status == "completed" else "✗" if task_status == "failed" else "○"
                    # ASCII fallback for non-Arial
                    if not _has_arial:
                        status_symbol = "OK" if task_status == "completed" else "XX" if task_status == "failed" else "--"
                    _cell(f"  {status_symbol} {task_desc}")
                pdf.ln(2)
            pdf.ln(4)
            pdf.set_draw_color(200, 200, 200)
            pdf.set_line_width(0.2)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(6)

        # ── Render markdown content ──────────────────────────────────────
        for line in report.split('\n'):
            try:
                s = line.rstrip()
                if s.startswith('# ') and not s.startswith('## '):
                    pdf.set_font(_FONT, "B", 15); pdf.set_text_color(25, 90, 190)
                    _cell(_clean_inline(s[2:])); pdf.ln(1)
                elif s.startswith('## ') and not s.startswith('### '):
                    pdf.ln(3); pdf.set_font(_FONT, "B", 12); pdf.set_text_color(20, 70, 160)
                    _cell(_clean_inline(s[3:])); pdf.ln(1)
                elif s.startswith('### '):
                    pdf.ln(2); pdf.set_font(_FONT, "B", 11); pdf.set_text_color(40, 40, 120)
                    _cell(_clean_inline(s[4:]))
                elif _re.fullmatch(r'-{3,}|\*{3,}|_{3,}', s.strip()):
                    pdf.set_draw_color(200, 200, 200); pdf.set_line_width(0.2)
                    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y()); pdf.ln(4)
                elif s.startswith(('- ', '* ')):
                    pdf.set_font(_FONT, "", 10); pdf.set_text_color(50, 50, 50)
                    pdf.cell(5, 7, "-", new_x="RIGHT", new_y="LAST")
                    _cell(" " + _clean_inline(s[2:].strip()))
                elif _re.match(r'^\d+\.\s', s):
                    pdf.set_font(_FONT, "", 10); pdf.set_text_color(50, 50, 50)
                    _cell("  " + _clean_inline(_re.sub(r'^\d+\.\s', '', s)))
                elif s == '':
                    pdf.ln(2)
                else:
                    pdf.set_font(_FONT, "", 10); pdf.set_text_color(50, 50, 50)
                    _cell(_clean_inline(s))
            except Exception:
                continue  # one bad line never aborts the file

        pdf.output(pdf_path)
        logger.info(f"[PDF] Report saved -> {pdf_path}")
        return pdf_path
    except Exception as e:
        logger.error(f"[PDF] Failed to generate PDF: {e}", exc_info=True)
        return None


# ── Helper: retry-safe single LLM call ──────────────────────────────────────
async def _llm_invoke_with_retry(
    llm: Any,
    messages: List[BaseMessage],
    label: str = "LLM",
    max_retries: int = 5,
    base_delay: int = 15,
) -> Any:
    """Invokes the LLM with exponential-backoff retry on 429 / ResourceExhausted."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            response = await llm.ainvoke(messages)
            return response
        except Exception as exc:
            last_exc = exc
            err_str = str(exc).lower()
            is_rate_limit = (
                "429" in err_str
                or "resource exhausted" in err_str
                or "rate limit" in err_str
                or "quota" in err_str
                or "ratelimit" in err_str
            )
            if is_rate_limit and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)  # 15 → 30 → 60 → 120s
                logger.warning(
                    f"{label}: rate-limited (429) on attempt {attempt + 1}/{max_retries}. "
                    f"Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)
            else:
                raise
    if last_exc:
        raise last_exc


# ── Helper: truncate message history to stay within context limits ───────────
_MAX_MESSAGE_HISTORY_LEN = 30  # keep at most this many messages (system + recent)
_MAX_SINGLE_MESSAGE_CHARS = 8000  # truncate any single message content beyond this


def _trim_messages(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Trim the message history to avoid exceeding the LLM context window.

    Strategy:
    - Always keep the first message (system prompt) and the last 10 messages.
    - Truncate individual message content that exceeds _MAX_SINGLE_MESSAGE_CHARS.
    - Drop middle messages if the total count exceeds _MAX_MESSAGE_HISTORY_LEN.
    """
    if not messages:
        return messages

    # Truncate individual messages
    trimmed = []
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if len(content) > _MAX_SINGLE_MESSAGE_CHARS:
            # Keep first and last portions
            half = _MAX_SINGLE_MESSAGE_CHARS // 2
            truncated = content[:half] + "\n\n... [truncated] ...\n\n" + content[-half:]
            # Create a new message of the same type with truncated content
            msg = msg.copy()
            msg.content = truncated
        trimmed.append(msg)

    # Drop middle messages if too many
    if len(trimmed) > _MAX_MESSAGE_HISTORY_LEN:
        # Keep first (system) + last N messages
        keep_tail = _MAX_MESSAGE_HISTORY_LEN - 1
        result = [trimmed[0]] + trimmed[-keep_tail:]
        logger.debug(
            f"Message history trimmed: {len(trimmed)} → {len(result)} messages"
        )
        return result
    return trimmed


def _analyze_task_intent(topic: str) -> Dict[str, Any]:
    """Intelligently analyze task to determine optimal execution strategy."""
    topic_lower = topic.lower()
    
    # Booking/Shopping keywords
    _booking_keywords = ("book", "buy", "purchase", "order", "reserve", "cheapest", "best price", "ticket", "flight", "train", "hotel")
    _shopping_keywords = ("price", "cost", "compare", "deal", "discount", "offer")
    _comparison_keywords = ("cheapest", "best", "compare", "versus", "vs", "which is better", "top 5", "top 10")
    _action_keywords = ("login", "sign up", "register", "fill", "submit", "create", "checkout")
    _research_keywords = ("what is", "how does", "explain", "analyze", "research", "summarize", "history", "guide")
    
    # Detect specific patterns
    is_booking_task = any(kw in topic_lower for kw in _booking_keywords)
    is_shopping_task = any(kw in topic_lower for kw in _shopping_keywords)
    is_comparison_task = any(kw in topic_lower for kw in _comparison_keywords)
    is_action_task = any(kw in topic_lower for kw in _action_keywords) and not any(kw in topic_lower for kw in _research_keywords)
    is_price_search = any(kw in topic_lower for kw in ("price", "cost", "cheapest", "₹", "$", "€", "£"))
    
    # Determine task category
    if is_booking_task or (is_shopping_task and is_comparison_task):
        category = "booking_comparison"
    elif is_action_task:
        category = "web_action"
    elif is_comparison_task:
        category = "comparison_research"
    else:
        category = "general_research"
    
    # Detect entities for smarter planning
    entities = {
        "cities": [],
        "dates": [],
        "products": [],
        "companies": []
    }
    
    # Simple entity extraction (can be enhanced with NER)
    import re
    # Dates: Feb 22, 22nd Feb, 2026-02-22
    date_patterns = [
        r'\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*)',
        r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\b',
        r'\d{4}-\d{2}-\d{2}',
    ]
    for pattern in date_patterns:
        entities["dates"].extend(re.findall(pattern, topic, re.IGNORECASE))
    
    # Indian cities (common)
    _indian_cities = ["Delhi", "Goa", "Mumbai", "Bangalore", "Chennai", "Kolkata", "Hyderabad", "Pune", "Ahmedabad", "Jaipur"]
    for city in _indian_cities:
        if city.lower() in topic_lower:
            entities["cities"].append(city)
    
    return {
        "category": category,
        "is_booking": is_booking_task,
        "is_shopping": is_shopping_task,
        "is_comparison": is_comparison_task,
        "is_price_search": is_price_search,
        "is_action": is_action_task,
        "entities": entities,
        "complexity": "simple" if len(topic.split()) <= 10 else "medium" if len(topic.split()) <= 20 else "complex"
    }


def _create_booking_comparison_plan(topic: str, intent_data: Dict[str, Any]) -> List[ResearchCategoryItem]:
    """Create optimized plan for booking/price comparison tasks."""
    # For booking tasks, visit multiple sites in parallel for comparison
    booking_sites = {
        "flight": ["ixigo.com", "makemytrip.com", "goibibo.com", "cleartrip.com"],
        "hotel": ["booking.com", "makemytrip.com", "goibibo.com", "agoda.com"],
        "train": ["irctc.co.in", "ixigo.com/trains", "makemytrip.com/railways"],
        "general": ["ixigo.com", "makemytrip.com", "goibibo.com"]
    }
    
    # Determine what's being booked
    topic_lower = topic.lower()
    if "flight" in topic_lower or "air" in topic_lower:
        sites = booking_sites["flight"]
        category_name = "Flight Price Comparison"
    elif "hotel" in topic_lower:
        sites = booking_sites["hotel"]
        category_name = "Hotel Price Comparison"
    elif "train" in topic_lower:
        sites = booking_sites["train"]
        category_name = "Train Booking Search"
    else:
        sites = booking_sites["general"]
        category_name = "Travel Price Comparison"
    
    # Create tasks for each site (max 3-4 sites)
    tasks = []
    for site in sites[:3]:  # Top 3 sites
        task_desc = f"""Visit {site} and search for: {topic}
Instructions:
1. Navigate directly to {site} (use go_to_url action)
2. Fill the search form using the new autocomplete and date picker actions if needed
3. Click search and wait for results (use smart_wait_for_element)
4. Extract prices using extract_prices_from_page or extract_table_data
5. Return the 3 cheapest options with: airline/operator, price, time, and booking link
6. If form filling fails after 2 attempts, try clicking elements using coordinates
7. If site is unresponsive, mark as failed and move to next site
CRITICAL: Do NOT give up until you have tried all available actions."""
        
        tasks.append(ResearchTaskItem(
            task_description=task_desc,
            status="pending",
            queries=None,
            result_summary=None,
        ))
    
    return [ResearchCategoryItem(category_name=category_name, tasks=tasks)]


async def planning_node(state: DeepResearchState) -> Dict[str, Any]:
    logger.info("--- Entering Planning Node ---")
    if state.get("stop_requested"):
        logger.info("Stop requested, skipping planning.")
        return {"stop_requested": True}

    llm = state["llm"]
    topic = state["topic"]
    existing_plan = state.get("research_plan")
    output_dir = state["output_dir"]
    # Smart plan sizing: short/simple queries get fewer tasks = faster execution
    _word_count = len(topic.split())
    MAX_CATEGORIES = 2 if _word_count <= 7 else 3 if _word_count <= 14 else 4
    MAX_TASKS_PER_CATEGORY = 2 if _word_count <= 7 else 3

    if existing_plan and (
            state.get("current_category_index", 0) > 0 or state.get("current_task_index_in_category", 0) > 0):
        logger.info("Resuming with existing plan.")
        _save_plan_to_md(existing_plan, output_dir)
        return {"research_plan": existing_plan}

    logger.info(f"Generating new research plan for topic: {topic}")

    # ══════════════════════════════════════════════════════════════════════════
    # INTELLIGENT TASK ANALYSIS — Detect booking/comparison tasks for smart planning
    # ══════════════════════════════════════════════════════════════════════════
    task_id = state.get("task_id")
    intent_analysis = _analyze_task_intent(topic)
    logger.info(f"Task intent: category={intent_analysis['category']}, "
                f"booking={intent_analysis['is_booking']}, "
                f"comparison={intent_analysis['is_comparison']}, "
                f"complexity={intent_analysis['complexity']}")
    
    # For booking/price comparison tasks, use optimized multi-source plan
    if intent_analysis["category"] == "booking_comparison":
        logger.info("🎯 Booking comparison task detected — using optimized multi-site plan")
        smart_plan = _create_booking_comparison_plan(topic, intent_analysis)
        _save_plan_to_md(smart_plan, output_dir)
        
        if task_id:
            with _STATE_LOCK:
                _TASK_TYPE_REGISTRY[task_id] = "action"  # Booking is action-based
        
        from src.agent.deep_research.deep_research_agent import _push_to_ui_log
        _push_to_ui_log(f"📋 Smart plan: {len(smart_plan[0]['tasks'])} sites for price comparison")
        
        return {
            "research_plan": smart_plan,
            "current_category_index": 0,
            "current_task_index_in_category": 0,
            "search_results": [],
            "task_type": "action",
        }

    # ── UNIFIED CLASSIFICATION + PLANNING (single LLM call instead of 3) ────
    # This avoids 429 rate limits by combining classify + intent + plan into one prompt.

    unified_prompt = f"""You are an expert research planner. Analyze the following task and produce a SINGLE JSON response.

TASK: "{topic}"

STEP 1 — CLASSIFY: Is this task RESEARCH (gather information, summarize, compare, analyse) or ACTION (login, book, buy, fill form, interact with a website, click, submit, create files, write code)?

STEP 2 — If ACTION: set task_type to "ACTION" and create a single-step execution plan.
         If RESEARCH: set task_type to "RESEARCH" and create a multi-category research plan.

RESPOND WITH EXACTLY THIS JSON STRUCTURE (no extra text):
{{
  "task_type": "RESEARCH" or "ACTION",
  "domain": "e.g. finance, travel, technology, health, education, general",
  "primary_goal": "one sentence: what the user actually wants to achieve",
  "output_style": "one of: comparative_table, narrative_report, bullet_summary, data_analysis",
  "strategy": "2-3 sentences on the best research approach",
  "plan": [
    {{
      "category_name": "Category Name",
      "tasks": ["Search: specific search query 1", "Search: specific search query 2"]
    }}
  ]
}}

RULES FOR THE PLAN:
- For ACTION tasks: exactly 1 category ("Direct Task Execution") with 1 task describing the full action.
- For RESEARCH tasks: AT MOST {MAX_CATEGORIES} categories, AT MOST {MAX_TASKS_PER_CATEGORY} tasks per category.
- Every task must be SPECIFIC, TARGETED, and directly answer what the user asked.
- Phrase each research task as a concrete web search query starting with "Search:".
- For investment/stock topics: research actual stocks, companies, metrics — not generic history.
- For travel topics: research destinations, costs, routes — not political history.
- For product topics: research specific products, prices, reviews — not brand history.

OUTPUT ONLY VALID JSON. No markdown, no backticks, no extra text."""

    unified_messages = [
        SystemMessage(content="You are a research planning assistant. Output ONLY valid JSON."),
        HumanMessage(content=unified_prompt),
    ]

    try:
        response = await _llm_invoke_with_retry(llm, unified_messages, label="UnifiedPlanning", max_retries=5, base_delay=15)
    except Exception as e:
        logger.error(f"Planning failed after all retries: {e}", exc_info=True)
        return {"error_message": f"Planning Error: {e}"}

    raw_content = response.content
    # Strip markdown code fences if present
    if raw_content.strip().startswith("```json"):
        raw_content = raw_content.strip()[7:-3].strip()
    elif raw_content.strip().startswith("```"):
        raw_content = raw_content.strip()[3:-3].strip()

    try:
        logger.debug(f"LLM unified response: {raw_content}")
        unified_data = json.loads(raw_content)

        task_type = unified_data.get("task_type", "RESEARCH").upper()
        is_action_task = "ACTION" in task_type
        logger.info(f"Task classified as: {'ACTION' if is_action_task else 'RESEARCH'} "
                     f"(domain={unified_data.get('domain')}, style={unified_data.get('output_style')})")

        # Store task type in registry so browser sub-agents can read it
        if task_id:
            with _STATE_LOCK:
                _TASK_TYPE_REGISTRY[task_id] = "action" if is_action_task else "research"

        if is_action_task:
            # Build action plan from the unified response
            logger.info("Action task detected — generating single-step direct-action plan.")
            action_plan = [
                ResearchCategoryItem(
                    category_name="Direct Task Execution",
                    tasks=[
                        ResearchTaskItem(
                            task_description=(
                                f"Navigate directly to the target website and complete the following task: {topic}. "
                                f"Use go_to_url to reach the site directly. Do NOT search Google. "
                                f"Complete all required interactions (login, form fill, button clicks) as instructed."
                            ),
                            status="pending",
                            queries=None,
                            result_summary=None,
                        )
                    ],
                )
            ]
            logger.info(f"[PLAN] ACTION task — 1 category, 1 task. Topic: '{topic[:60]}'")
            _save_plan_to_md(action_plan, output_dir)
            return {
                "research_plan": action_plan,
                "current_category_index": 0,
                "current_task_index_in_category": 0,
                "search_results": [],
                "task_type": "action",
            }

        # RESEARCH task — build plan from the unified response
        plan_data = unified_data.get("plan", [])

        new_plan: List[ResearchCategoryItem] = []
        for cat_idx, category_data in enumerate(plan_data):
            # A2: Hard cap on plan size
            if cat_idx >= MAX_CATEGORIES:
                logger.info(f"Plan capped at {MAX_CATEGORIES} categories.")
                break
            if not isinstance(category_data,
                              dict) or "category_name" not in category_data or "tasks" not in category_data:
                logger.warning(f"Skipping invalid category data: {category_data}")
                continue

            tasks: List[ResearchTaskItem] = []
            for task_idx, task_desc in enumerate(category_data["tasks"]):
                if task_idx >= MAX_TASKS_PER_CATEGORY:
                    logger.info(f"Tasks capped at {MAX_TASKS_PER_CATEGORY} per category.")
                    break
                if isinstance(task_desc, str):
                    tasks.append(
                        ResearchTaskItem(
                            task_description=task_desc,
                            status="pending",
                            queries=None,
                            result_summary=None,
                        )
                    )
                else:  # Sometimes LLM puts tasks as {"task": "description"}
                    if isinstance(task_desc, dict) and "task_description" in task_desc:
                        tasks.append(
                            ResearchTaskItem(
                                task_description=task_desc["task_description"],
                                status="pending",
                                queries=None,
                                result_summary=None,
                            )
                        )
                    elif isinstance(task_desc, dict) and "task" in task_desc:  # common LLM mistake
                        tasks.append(
                            ResearchTaskItem(
                                task_description=task_desc["task"],
                                status="pending",
                                queries=None,
                                result_summary=None,
                            )
                        )
                    else:
                        logger.warning(
                            f"Skipping invalid task data: {task_desc} in category {category_data['category_name']}")

            new_plan.append(
                ResearchCategoryItem(
                    category_name=category_data["category_name"],
                    tasks=tasks,
                )
            )

        if not new_plan:
            logger.error("LLM failed to generate a valid plan structure from JSON.")
            return {"error_message": "Failed to generate research plan structure."}

        logger.info(f"[PLAN] {len(new_plan)} categories, {sum(len(c['tasks']) for c in new_plan)} total tasks for topic: '{topic[:60]}'")
        logger.info(f"[PLAN] Categories: {[c['category_name'] for c in new_plan]}")
        _save_plan_to_md(new_plan, output_dir)  # Save the hierarchical plan

        return {
            "research_plan": new_plan,
            "current_category_index": 0,
            "current_task_index_in_category": 0,
            "search_results": [],
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON from LLM for plan: {e}. Response was: {raw_content}", exc_info=True)
        return {"error_message": f"Planning JSON Error: LLM generated invalid JSON for research plan: {e}"}
    except Exception as e:
        logger.error(f"Error during planning: {e}", exc_info=True)
        return {"error_message": f"Planning Error: {e}"}


async def research_execution_node(state: DeepResearchState) -> Dict[str, Any]:
    logger.info("--- Entering Research Execution Node ---")
    if state.get("stop_requested"):
        logger.info("Stop requested, skipping research execution.")
        return {
            "stop_requested": True,
            "current_category_index": state["current_category_index"],
            "current_task_index_in_category": state["current_task_index_in_category"],
        }

    plan = state["research_plan"]
    cat_idx = state["current_category_index"]
    task_idx = state["current_task_index_in_category"]
    llm = state["llm"]
    tools = state["tools"]
    output_dir = str(state["output_dir"])
    task_id = state["task_id"]  # For _AGENT_STOP_FLAGS

    # This check should ideally be handled by `should_continue`
    if not plan or cat_idx >= len(plan):
        logger.info("Research plan complete or categories exhausted.")
        return {}  # should route to synthesis

    current_category = plan[cat_idx]
    if task_idx >= len(current_category["tasks"]):
        logger.info(f"All tasks in category '{current_category['category_name']}' completed. Moving to next category.")
        # This logic is now effectively handled by should_continue and the index updates below
        # The next iteration will be caught by should_continue or this node with updated indices
        return {
            "current_category_index": cat_idx + 1,
            "current_task_index_in_category": 0,
            "messages": state["messages"]  # Pass messages along
        }

    current_task = current_category["tasks"][task_idx]

    if current_task["status"] == "completed":
        logger.info(
            f"Task '{current_task['task_description']}' in category '{current_category['category_name']}' already completed. Skipping.")
        # Logic to find next task
        next_task_idx = task_idx + 1
        next_cat_idx = cat_idx
        if next_task_idx >= len(current_category["tasks"]):
            next_cat_idx += 1
            next_task_idx = 0
        return {
            "current_category_index": next_cat_idx,
            "current_task_index_in_category": next_task_idx,
            "messages": state["messages"]  # Pass messages along
        }

    logger.info(
        f"[TASK {task_idx+1}] Category '{current_category['category_name']}' | "
        f"Task: '{current_task['task_description'][:80]}'"
    )

    # ── ACTION fast-path: run browser agents directly — supports parallel browsers ─────
    # This prevents the LLM from stripping booking constraints into generic search queries.
    if state.get("task_type") == "action":
        with _STATE_LOCK:
            _stop_ev = _AGENT_STOP_FLAGS.get(task_id) or threading.Event()

        # Determine how many parallel browsers are available
        _browser_tool = next((t for t in tools if t.name == "parallel_browser_search"), None)
        _max_parallel = 1
        if _browser_tool and hasattr(_browser_tool, "description"):
            import re as _re
            _m = _re.search(r"up to (\d+) searches", _browser_tool.description)
            if _m:
                _max_parallel = int(_m.group(1))

        logger.info(f"[ACTION] Direct browser execution for: '{state['topic'][:80]}' (parallel={_max_parallel})")

        if _max_parallel > 1:
            # ── PARALLEL ACTION: split the task across multiple browsers via _run_browser_search_tool ──
            # Each browser gets the same full task — they'll each try different sites
            # because run_single_browser_task's ACTION prompt has a multi-site strategy.
            # We create slightly varied queries to cover different angles.
            _queries = [state["topic"]]  # First browser: original full topic
            for i in range(1, _max_parallel):
                _queries.append(f"{state['topic']} (attempt alternate site #{i+1})")

            try:
                _action_results = await _run_browser_search_tool(
                    queries=_queries,
                    task_id=task_id,
                    llm=llm,
                    browser_config=state["browser_config"],
                    stop_event=_stop_ev,
                    max_parallel_browsers=_max_parallel,
                    mcp_server_config=state.get("mcp_server_config"),
                    global_task_type="action",
                )
            except Exception as _ae:
                logger.error(f"[ACTION] Parallel execution exception: {_ae}", exc_info=True)
                _action_results = [{"query": state["topic"], "status": "failed", "error": str(_ae)}]

            # Merge results — pick the best completed result
            _best_result = None
            for r in _action_results:
                if r.get("status") == "completed" and r.get("result"):
                    _best_result = r
                    break
            if not _best_result and _action_results:
                _best_result = _action_results[0]

            current_task["status"] = "completed" if any(r.get("status") == "completed" for r in _action_results) else "failed"
            current_task["result_summary"] = str((_best_result or {}).get("result") or (_best_result or {}).get("error", ""))[:500]
        else:
            # ── SINGLE ACTION: legacy path — one browser ──
            try:
                _action_result = await run_single_browser_task(
                    task_query=state["topic"],
                    task_id=task_id,
                    llm=llm,
                    browser_config=state["browser_config"],
                    stop_event=_stop_ev,
                    mcp_server_config=state.get("mcp_server_config"),
                    global_task_type="action",
                )
            except Exception as _ae:
                logger.error(f"[ACTION] Direct execution exception: {_ae}", exc_info=True)
                _action_result = {"query": state["topic"], "status": "failed", "error": str(_ae)}
            _action_results = [_action_result]

            current_task["status"] = "completed" if _action_result.get("status") == "completed" else "failed"
            current_task["result_summary"] = str(_action_result.get("result") or _action_result.get("error", ""))[:500]

        _save_plan_to_md(plan, output_dir)

        _cur_results = state.get("search_results", [])
        for r in _action_results:
            _cur_results.append({
                "query":     r.get("query", state["topic"]),
                "result":    r.get("result"),
                "status":    r.get("status", "failed"),
                "tool_name": "direct_action",
            })
        _save_search_results_to_json(_cur_results, output_dir)
        logger.info(f"[ACTION] Execution complete. {len(_action_results)} browser(s) used.")
        return {
            "research_plan":                  plan,
            "search_results":                 _cur_results,
            "current_category_index":         cat_idx + 1,
            "current_task_index_in_category": 0,
            "messages":                       state["messages"],
        }
    # ────────────────────────────────────────────────────────────────

    llm_with_tools = llm.bind_tools(tools)

    # Construct messages for LLM invocation
    # B3: Inject a compact summary of findings gathered so far
    prior_summary = ""
    existing_results = state.get("search_results", [])
    if existing_results:
        completed = [r for r in existing_results if r.get("status") == "completed" and r.get("result")]
        if completed:
            snippets = []
            for r in completed[-5:]:  # last 5 results max to keep prompt small
                snippet = str(r.get("result", ""))[:200].replace("\n", " ")
                snippets.append(f"- {r.get('query', 'task')}: {snippet}...")
            prior_summary = (
                "\n\nPreviously gathered research (do NOT duplicate these):\n"
                + "\n".join(snippets)
                + "\n"
            )

    # Determine max_parallel_browsers from the browser_search tool description (stored in closure)
    _browser_tool = next((t for t in tools if t.name == "parallel_browser_search"), None)
    _max_q = 1
    if _browser_tool and hasattr(_browser_tool, "description"):
        import re as _re
        _m = _re.search(r"up to (\d+) searches", _browser_tool.description)
        if _m:
            _max_q = int(_m.group(1))

    task_prompt_content = (
        f"Current Research Category: {current_category['category_name']}\n"
        f"Specific Task: {current_task['task_description']}\n"
        f"{prior_summary}\n"
        f"You MUST call the 'parallel_browser_search' tool to gather web information for this task. "
        f"Provide EXACTLY {_max_q} distinct, focused search queries relevant to this task. "
        f"Each query should cover a DIFFERENT aspect, angle, or source for the task. "
        f"Do NOT respond with plain text only — always invoke 'parallel_browser_search'. "
        f"Using all {_max_q} queries is STRONGLY PREFERRED for thorough research."
    )
    current_task_message_history = [
        HumanMessage(content=task_prompt_content)
    ]
    if not state["messages"]:  # First actual execution message
        invocation_messages = [
                                  SystemMessage(
                                      content="You are a research assistant executing one task of a research plan. Focus on the current task only."),
                              ] + current_task_message_history
    else:
        invocation_messages = _trim_messages(state["messages"]) + current_task_message_history

    try:
        logger.info(f"Invoking LLM with tools for task: {current_task['task_description']}")
        ai_response: BaseMessage = await llm_with_tools.ainvoke(invocation_messages)
        logger.info("LLM invocation complete.")

        tool_results = []
        executed_tool_names = []
        current_search_results = state.get("search_results", [])  # Get existing search results

        if not isinstance(ai_response, AIMessage) or not ai_response.tool_calls:
            # LLM skipped the tool call — retry once with a stronger prompt
            logger.warning(
                f"LLM did not call any tool for task '{current_task['task_description']}'. "
                f"Re-prompting with forced tool instruction..."
            )
            retry_messages = invocation_messages + [
                ai_response,
                HumanMessage(content=(
                    "You MUST use the 'parallel_browser_search' tool NOW. "
                    "Do NOT respond with text only. Call the tool with search queries. "
                    f"Generate {_max_q} search queries and invoke parallel_browser_search immediately."
                )),
            ]
            try:
                ai_response = await llm_with_tools.ainvoke(retry_messages)
            except Exception:
                pass  # If retry fails, fall through to the completion logic below

        if not isinstance(ai_response, AIMessage) or not ai_response.tool_calls:
            # Still no tool call after retry — mark as completed and advance
            logger.warning(
                f"LLM still did not call any tool after retry for '{current_task['task_description']}'. "
                f"Using text response."
            )
            current_task["status"] = "completed"
            current_task["result_summary"] = (
                f"LLM provided text response (no tool call): {ai_response.content[:500]}"
            )
            _save_plan_to_md(plan, output_dir)
            _next_task_idx = task_idx + 1
            _next_cat_idx = cat_idx
            if _next_task_idx >= len(current_category["tasks"]):
                _next_cat_idx += 1
                _next_task_idx = 0
            return {
                "research_plan": plan,
                "current_category_index": _next_cat_idx,
                "current_task_index_in_category": _next_task_idx,
                "messages": state["messages"] + current_task_message_history + [ai_response],
            }
        else:
            # Process tool calls
            for tool_call in ai_response.tool_calls:
                tool_name = tool_call.get("name")
                tool_args = tool_call.get("args", {})
                tool_call_id = tool_call.get("id")

                logger.info(f"LLM requested tool call: {tool_name} with args: {tool_args}")
                executed_tool_names.append(tool_name)
                selected_tool = next((t for t in tools if t.name == tool_name), None)

                if not selected_tool:
                    logger.error(f"LLM called tool '{tool_name}' which is not available.")
                    tool_results.append(
                        ToolMessage(content=f"Error: Tool '{tool_name}' not found.", tool_call_id=tool_call_id))
                    continue

                try:
                    with _STATE_LOCK:
                        stop_event = _AGENT_STOP_FLAGS.get(task_id)
                    if stop_event and stop_event.is_set():
                        logger.info(f"Stop requested before executing tool: {tool_name}")
                        current_task["status"] = "pending"  # Or a new "stopped" status
                        _save_plan_to_md(plan, output_dir)
                        return {"stop_requested": True, "research_plan": plan, "current_category_index": cat_idx,
                                "current_task_index_in_category": task_idx}

                    logger.info(f"Executing tool: {tool_name}")
                    tool_output = await selected_tool.ainvoke(tool_args)
                    logger.info(f"Tool '{tool_name}' executed successfully.")

                    if tool_name == "parallel_browser_search":
                        current_search_results.extend(tool_output)  # tool_output is List[Dict]
                    else:  # For other tools, we might need specific handling or just log
                        logger.info(f"Result from tool '{tool_name}': {str(tool_output)[:200]}...")
                        # Storing non-browser results might need a different structure or key in search_results
                        current_search_results.append(
                            {"tool_name": tool_name, "args": tool_args, "output": str(tool_output),
                             "status": "completed"})

                    tool_results.append(ToolMessage(content=json.dumps(tool_output), tool_call_id=tool_call_id))

                except Exception as e:
                    logger.error(f"Error executing tool '{tool_name}': {e}", exc_info=True)
                    tool_results.append(
                        ToolMessage(content=f"Error executing tool {tool_name}: {e}", tool_call_id=tool_call_id))
                    current_search_results.append(
                        {"tool_name": tool_name, "args": tool_args, "status": "failed", "error": str(e)})

            # After processing all tool calls for this task
            step_failed_tool_execution = any("Error:" in str(tr.content) for tr in tool_results)
            # Consider a task successful if a browser search was attempted and didn't immediately error out during call
            # The browser search itself returns status for each query.
            browser_tool_attempted_successfully = "parallel_browser_search" in executed_tool_names and not step_failed_tool_execution

            if step_failed_tool_execution:
                current_task["status"] = "failed"
                current_task[
                    "result_summary"] = f"Tool execution failed. Errors: {[tr.content for tr in tool_results if 'Error' in str(tr.content)]}"
            elif executed_tool_names:  # If any tool was called
                current_task["status"] = "completed"
                current_task["result_summary"] = f"Executed tool(s): {', '.join(executed_tool_names)}."
                # TODO: Could ask LLM to summarize the tool_results for this task if needed, rather than just listing tools.
            else:  # No tool calls but AI response had .tool_calls structure (empty)
                current_task["status"] = "failed"  # Or a more specific status
                current_task["result_summary"] = "LLM prepared for tool call but provided no tools."

        # Save progress
        _save_plan_to_md(plan, output_dir)
        _save_search_results_to_json(current_search_results, output_dir)

        # Determine next indices
        next_task_idx = task_idx + 1
        next_cat_idx = cat_idx
        if next_task_idx >= len(current_category["tasks"]):
            next_cat_idx += 1
            next_task_idx = 0

        updated_messages = state["messages"] + current_task_message_history + [ai_response] + tool_results

        return {
            "research_plan": plan,
            "search_results": current_search_results,
            "current_category_index": next_cat_idx,
            "current_task_index_in_category": next_task_idx,
            "messages": updated_messages,
        }

    except Exception as e:
        logger.error(f"Unhandled error during research execution for task '{current_task['task_description']}': {e}",
                     exc_info=True)
        current_task["status"] = "failed"
        _save_plan_to_md(plan, output_dir)
        # Determine next indices even on error to attempt to move on
        next_task_idx = task_idx + 1
        next_cat_idx = cat_idx
        if next_task_idx >= len(current_category["tasks"]):
            next_cat_idx += 1
            next_task_idx = 0
        return {
            "research_plan": plan,
            "current_category_index": next_cat_idx,
            "current_task_index_in_category": next_task_idx,
            "error_message": f"Core Execution Error on task '{current_task['task_description']}': {e}",
            "messages": state["messages"] + current_task_message_history  # Preserve messages up to error
        }


async def synthesis_node(state: DeepResearchState) -> Dict[str, Any]:
    """Synthesizes the final report from the collected search results."""
    logger.info("--- Entering Synthesis Node ---")
    if state.get("stop_requested"):
        logger.info("Stop requested, skipping synthesis.")
        return {"stop_requested": True}

    llm = state["llm"]
    topic = state["topic"]
    search_results = state.get("search_results", [])
    output_dir = state["output_dir"]
    plan = state["research_plan"]  # Include plan for context

    if not search_results:
        logger.warning("No search results found to synthesize report.")
        report = f"# Research Report: {topic}\n\nNo information was gathered during the research process."
        _save_report_to_md(report, output_dir)
        return {"final_report": report}

    logger.info(f"[SYNTHESIS] Building final report from {len(search_results)} findings for topic: '{topic[:60]}'")

    # Prepare context for the LLM
    # Format search results nicely, maybe group by query or original plan step
    formatted_results = ""
    references = {}
    ref_count = 1
    import re as _url_re
    for i, result_entry in enumerate(search_results):
        query = result_entry.get("query", "Unknown Query")  # From parallel_browser_search
        tool_name = result_entry.get("tool_name")  # From other tools
        status = result_entry.get("status", "unknown")
        result_data = result_entry.get("result")  # From BrowserUseAgent's final_result
        tool_output_str = result_entry.get("output")  # From other tools

        if status == "completed" and result_data:
            # Extract URLs from the result text for references
            result_str = str(result_data)
            urls_found = _url_re.findall(r'https?://[^\s\)\]\"\'\,]+', result_str)
            for url in urls_found:
                url = url.rstrip('.,;:')
                if url not in [r.get("url") for r in references.values()]:
                    # Extract domain as a title fallback
                    domain_match = _url_re.search(r'https?://(?:www\.)?([^/]+)', url)
                    domain_title = domain_match.group(1) if domain_match else url
                    references[ref_count] = {"id": ref_count, "title": domain_title, "url": url}
                    ref_count += 1

            # Truncate very long individual results to prevent context overflow
            result_text = str(result_data)
            if len(result_text) > 3000:
                result_text = result_text[:2500] + "\n\n... [truncated] ...\n\n" + result_text[-500:]

            formatted_results += f'### Finding from Web Search Query: "{query}"\n'
            formatted_results += f"- **Summary:**\n{result_text}\n"
            formatted_results += "---\n"
        elif tool_name and tool_name != "parallel_browser_search" and status == "completed" and tool_output_str:
            formatted_results += f'### Finding from Tool: "{tool_name}" (Args: {result_entry.get("args")})\n'
            formatted_results += f"- **Output:**\n{tool_output_str}\n"
            formatted_results += "---\n"
        elif status == "failed":
            error = result_entry.get("error")
            q_or_t = f"Query: \"{query}\"" if query != "Unknown Query" else f"Tool: \"{tool_name}\""
            formatted_results += f'### Failed {q_or_t}\n'
            formatted_results += f"- **Error:** {error}\n"
            formatted_results += "---\n"

    # Prepare the research plan context
    plan_summary = "\nResearch Plan Followed:\n"
    for cat_idx, category in enumerate(plan):
        plan_summary += f"\n#### Category {cat_idx + 1}: {category['category_name']}\n"
        for task_idx, task in enumerate(category['tasks']):
            marker = "[x]" if task["status"] == "completed" else "[ ]" if task["status"] == "pending" else "[-]"
            plan_summary += f"  - {marker} {task['task_description']}\n"

    # Truncate total formatted_results to prevent context window overflow
    _MAX_SYNTHESIS_CHARS = 30000
    if len(formatted_results) > _MAX_SYNTHESIS_CHARS:
        logger.warning(
            f"[SYNTHESIS] Formatted results too long ({len(formatted_results)} chars), "
            f"truncating to {_MAX_SYNTHESIS_CHARS} chars."
        )
        formatted_results = (
            formatted_results[:_MAX_SYNTHESIS_CHARS - 200]
            + "\n\n... [additional findings truncated for brevity] ...\n"
        )

    # ── Synthesis Chain-of-Thought: reflect before writing ──────────────────
    # Ask the LLM to think about the best way to present findings BEFORE
    # writing the report. This produces sharper, more structured output.
    synthesis_cot = ""
    try:
        synthesis_think_messages = [
            SystemMessage(content=(
                "You are a senior research editor. Before writing the report, think carefully:\n"
                "1. What are the 3-5 most IMPORTANT findings from the data?\n"
                "2. What is MISSING or unclear in the research?\n"
                "3. What report structure would answer the user's question most directly?\n"
                "4. Should the conclusion include a recommendation or just a summary?\n"
                "5. What one key insight should the user walk away with?\n\n"
                "Be concise — max 6 bullets. End with: REPORT_STRUCTURE: <brief outline>"
            )),
            HumanMessage(content=(
                f'Topic: "{topic}"\n\n'
                f'Research Findings (summary):\n{formatted_results[:3000]}'
            )),
        ]
        synthesis_think_resp = await _llm_invoke_with_retry(
            llm, synthesis_think_messages, label="SynthesisCoT", max_retries=2, base_delay=8
        )
        synthesis_cot = synthesis_think_resp.content.strip()
        logger.info(f"[SYNTHESIS-COT] Pre-synthesis reasoning done. Length={len(synthesis_cot)}")
    except Exception as e:
        logger.warning(f"Synthesis chain-of-thought failed (non-fatal): {e}")
        synthesis_cot = ""

    cot_directive = (
        f"\n\nPRE-SYNTHESIS REASONING (use this to guide your report structure and emphasis):\n{synthesis_cot}\n"
        if synthesis_cot else ""
    )

    synthesis_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a research analyst. Write a concise, well-structured Markdown report from the collected findings below. "
                "Use these sections: ## Introduction, ## Key Findings (subsections per category), ## Conclusion. "
                "Be factual and specific — include real numbers, names, prices, and comparisons where available. "
                "If a finding is incomplete or failed, note it in one line. Be direct; omit filler phrases.",
            ),
            (
                "human",
                f"""
            **Research Topic:** {topic}
            {cot_directive}
            {plan_summary}

            **Collected Findings:**
            ```
            {formatted_results}
            ```

            Please generate the final research report in Markdown format based **only** on the information above.
            """,
            ),
        ]
    )

    try:
        response = await _llm_invoke_with_retry(
            llm,
            synthesis_prompt.format_prompt(
                topic=topic,
                plan_summary=plan_summary,
                formatted_results=formatted_results,
            ).to_messages(),
            label="Synthesis",
            max_retries=5,
            base_delay=15,
        )
        final_report_md = response.content

        # Append the reference list automatically to the end of the generated markdown
        if references:
            report_references_section = "\n\n## References\n\n"
            # Sort refs by ID for consistent output
            sorted_refs = sorted(references.values(), key=lambda x: x["id"])
            for ref in sorted_refs:
                report_references_section += (
                    f"[{ref['id']}] {ref['title']} - {ref['url']}\n"
                )
            final_report_md += report_references_section

        logger.info("[SYNTHESIS] Report written. Saving MD + PDF...")
        _save_report_to_md(final_report_md, output_dir)
        _save_report_to_pdf(final_report_md, output_dir, topic=topic, plan=plan)
        logger.info("[SYNTHESIS COMPLETE] Report and PDF ready for download.")
        return {"final_report": final_report_md}

    except Exception as e:
        logger.error(f"Error during report synthesis: {e}", exc_info=True)
        # ── Error Recovery: generate a basic fallback report from raw results ──
        logger.info("Attempting fallback report generation from raw search results...")
        try:
            completed_results = [r for r in search_results if r.get("status") == "completed" and r.get("result")]
            failed_results = [r for r in search_results if r.get("status") == "failed"]
            if completed_results or search_results:
                fallback_lines = [
                    f"# Research Report: {topic}",
                    "",
                    f"> ⚠️ Note: Full AI synthesis failed ({type(e).__name__}). This is a raw data report.",
                    "",
                    f"## Research Summary ({len(completed_results)} of {len(search_results)} tasks succeeded)",
                    "",
                ]
                for i, r in enumerate(completed_results, 1):
                    fallback_lines.append(f"### Finding {i}: {r.get('query', 'Research Task')}")
                    result_text = str(r.get('result', '')).strip()
                    if result_text:
                        fallback_lines.append(result_text)
                    fallback_lines.append("")
                if failed_results:
                    fallback_lines.append("---")
                    fallback_lines.append(f"## Tasks That Could Not Be Completed ({len(failed_results)})")
                    for r in failed_results:
                        fallback_lines.append(f"- {r.get('query', 'Unknown task')}: `{r.get('error', 'Unknown error')}`")
                fallback_report = "\n".join(fallback_lines)
                _save_report_to_md(fallback_report, output_dir)
                _save_report_to_pdf(fallback_report, output_dir, topic=topic, plan=plan)
                logger.info("Fallback report generated and saved.")
                return {"final_report": fallback_report}
        except Exception as fallback_exc:
            logger.error(f"Fallback report generation also failed: {fallback_exc}")
        return {"error_message": f"LLM Error during synthesis: {e}"}


# --- Langgraph Edges and Conditional Logic ---


def should_continue(state: DeepResearchState) -> str:
    logger.info("--- Evaluating Condition: Should Continue? ---")
    if state.get("stop_requested"):
        logger.info("Stop requested, routing to END.")
        return "end_run"
    if state.get("error_message"):
        logger.warning(f"Error detected: {state['error_message']}. Routing to END.")
        return "end_run"

    # B5: check whether we've hit the max_tasks_total budget
    _max_tasks = state.get("max_tasks_total", 12)
    _completed_tasks = sum(
        1
        for cat in (state.get("research_plan") or [])
        for task in cat.get("tasks", [])
        if task.get("status") in ("completed", "failed")
    )
    if _completed_tasks >= _max_tasks:
        logger.info(f"Max tasks limit reached ({_completed_tasks}/{_max_tasks}). Routing to Synthesis.")
        return "synthesize_report"

    plan = state.get("research_plan")
    cat_idx = state.get("current_category_index", 0)
    task_idx = state.get("current_task_index_in_category", 0)

    if not plan:
        logger.warning("No research plan found. Routing to END.")
        return "end_run"

    # Check if the current indices point to a valid pending task
    if cat_idx < len(plan):
        current_category = plan[cat_idx]
        if task_idx < len(current_category["tasks"]):
            # We are trying to execute the task at plan[cat_idx]["tasks"][task_idx]
            # The research_execution_node will handle if it's already completed.
            logger.info(
                f"Plan has potential pending tasks (next up: Category {cat_idx}, Task {task_idx}). Routing to Research Execution."
            )
            return "execute_research"
        else:  # task_idx is out of bounds for current category, means we need to check next category
            if cat_idx + 1 < len(plan):  # If there is a next category
                logger.info(
                    f"Finished tasks in category {cat_idx}. Moving to category {cat_idx + 1}. Routing to Research Execution."
                )
                # research_execution_node will update state to {current_category_index: cat_idx + 1, current_task_index_in_category: 0}
                # Or rather, the previous execution node already set these indices to the start of the next category.
                return "execute_research"

    # If we've gone through all categories and tasks (cat_idx >= len(plan))
    logger.info("All plan categories and tasks processed or current indices are out of bounds. Routing to Synthesis.")
    return "synthesize_report"


# --- DeepSearchAgent Class ---


class DeepResearchAgent:
    def __init__(
            self,
            llm: Any,
            browser_config: Dict[str, Any],
            mcp_server_config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initializes the DeepSearchAgent.

        Args:
            llm: The Langchain compatible language model instance.
            browser_config: Configuration dictionary for the BrowserUseAgent tool.
                            Example: {"headless": True, "window_width": 1280, ...}
            mcp_server_config: Optional configuration for the MCP client.
        """
        self.llm = llm
        self.browser_config = browser_config
        self.mcp_server_config = mcp_server_config
        self.mcp_client = None
        self.stopped = False
        self.graph = self._compile_graph()
        self.current_task_id: Optional[str] = None
        self.stop_event: Optional[threading.Event] = None
        self.runner: Optional[asyncio.Task] = None  # To hold the asyncio task for run

    async def _setup_tools(
            self, task_id: str, stop_event: threading.Event, max_parallel_browsers: int = 1
    ) -> List[Tool]:
        """Sets up browser search tool and optional MCP tools.
        NOTE: Filesystem tools (ReadFileTool, WriteFileTool, ListDirectoryTool) are intentionally
        excluded to prevent the LLM from reading local files (e.g. README.md) as research output.
        """
        tools = []  # No filesystem tools — browser search only
        browser_use_tool = create_browser_search_tool(
            llm=self.llm,
            browser_config=self.browser_config,
            task_id=task_id,
            stop_event=stop_event,
            max_parallel_browsers=max_parallel_browsers,
            mcp_server_config=self.mcp_server_config,
        )
        tools += [browser_use_tool]
        # Add MCP tools if config is provided
        if self.mcp_server_config:
            try:
                logger.info("Setting up MCP client and tools...")
                if not self.mcp_client:
                    self.mcp_client = await setup_mcp_client_and_tools(
                        self.mcp_server_config
                    )
                if self.mcp_client:
                    # Load tools per-server so a single failing server doesn't block the rest
                    total_mcp = 0
                    for server_name in list(self.mcp_client.connections.keys()):
                        try:
                            server_tools = await self.mcp_client.get_tools(server_name=server_name)
                            tools.extend(server_tools)
                            total_mcp += len(server_tools)
                        except Exception as srv_err:
                            # Flatten ExceptionGroup to one clean line
                            root: BaseException = srv_err
                            while hasattr(root, 'exceptions') and getattr(root, 'exceptions', None):
                                root = root.exceptions[0]  # type: ignore[attr-defined]
                            logger.warning(
                                f"Skipped MCP server '{server_name}' "
                                f"({type(root).__name__}: {root}). "
                                f"Check that the server package is installed and configured correctly."
                            )
                    logger.info(f"Loaded {total_mcp} MCP tool(s) for deep research.")
                else:
                    logger.warning("MCP client setup returned None — skipping MCP tools.")
            except Exception as e:
                logger.error(f"Failed to set up MCP tools: {e}")
        tools_map = {tool.name: tool for tool in tools}
        return tools_map.values()

    async def close_mcp_client(self):
        if self.mcp_client:
            try:
                # langchain-mcp-adapters >= 0.1.0 dropped context-manager support;
                # __aexit__ exists but raises NotImplementedError.
                # Try disconnect_all first, then aclose, then __aexit__ as last resort.
                if hasattr(self.mcp_client, 'disconnect_all'):
                    await self.mcp_client.disconnect_all()
                elif hasattr(self.mcp_client, 'aclose'):
                    await self.mcp_client.aclose()
                elif hasattr(self.mcp_client, 'close'):
                    await self.mcp_client.close()
                # Do NOT call __aexit__ — it raises NotImplementedError in >= 0.1.0
            except (NotImplementedError, Exception):
                pass  # Cleanup best-effort
            self.mcp_client = None

    def _compile_graph(self) -> StateGraph:
        """Compiles the Langgraph state machine."""
        workflow = StateGraph(DeepResearchState)

        # Add nodes
        workflow.add_node("plan_research", planning_node)
        workflow.add_node("execute_research", research_execution_node)
        workflow.add_node("synthesize_report", synthesis_node)
        workflow.add_node(
            "end_run", lambda state: logger.info("--- Reached End Run Node ---") or {}
        )  # Simple end node

        # Define edges
        workflow.set_entry_point("plan_research")

        workflow.add_edge(
            "plan_research", "execute_research"
        )  # Always execute after planning

        # Conditional edge after execution
        workflow.add_conditional_edges(
            "execute_research",
            should_continue,
            {
                "execute_research": "execute_research",  # Loop back if more steps
                "synthesize_report": "synthesize_report",  # Move to synthesis if done
                "end_run": "end_run",  # End if stop requested or error
            },
        )

        workflow.add_edge("synthesize_report", "end_run")  # End after synthesis

        app = workflow.compile()
        return app

    async def run(
            self,
            topic: str,
            task_id: Optional[str] = None,
            save_dir: str = "./tmp/deep_research",
            max_parallel_browsers: int = 1,
            max_tasks: int = 12,
    ) -> Dict[str, Any]:
        """
        Starts the deep research process (Async Generator Version).

        Args:
            topic: The research topic.
            task_id: Optional existing task ID to resume. If None, a new ID is generated.

        Yields:
             Intermediate state updates or messages during execution.
        """
        if self.runner and not self.runner.done():
            logger.warning(
                "Agent is already running. Please stop the current task first."
            )
            # Return an error status instead of yielding
            return {
                "status": "error",
                "message": "Agent already running.",
                "task_id": self.current_task_id,
            }

        self.current_task_id = task_id if task_id else str(uuid.uuid4())
        safe_root_dir = "./tmp/deep_research"
        normalized_save_dir = os.path.normpath(save_dir)
        if not normalized_save_dir.startswith(os.path.abspath(safe_root_dir)):
            logger.warning(f"Unsafe save_dir detected: {save_dir}. Using default directory.")
            normalized_save_dir = os.path.abspath(safe_root_dir)
        output_dir = os.path.join(normalized_save_dir, self.current_task_id)
        os.makedirs(output_dir, exist_ok=True)

        logger.info(
            f"[AsyncGen] Starting research task ID: {self.current_task_id} for topic: '{topic}'"
        )
        logger.info(f"[AsyncGen] Output directory: {output_dir}")

        self.stop_event = threading.Event()
        with _STATE_LOCK:
            _AGENT_STOP_FLAGS[self.current_task_id] = self.stop_event
        agent_tools = await self._setup_tools(
            self.current_task_id, self.stop_event, max_parallel_browsers
        )
        initial_state: DeepResearchState = {
            "task_id": self.current_task_id,
            "topic": topic,
            "research_plan": [],
            "search_results": [],
            "messages": [],
            "llm": self.llm,
            "tools": agent_tools,
            "output_dir": Path(output_dir),
            "browser_config": self.browser_config,
            "final_report": None,
            "current_category_index": 0,
            "current_task_index_in_category": 0,
            "stop_requested": False,
            "error_message": None,
            "max_tasks_total": max(1, int(max_tasks)),  # B5: propagate task budget
            "task_type": "research",  # A1: default; planning_node will update
            "mcp_server_config": self.mcp_server_config,
        }

        if task_id:
            logger.info(f"Attempting to resume task {task_id}...")
            loaded_state = _load_previous_state(task_id, output_dir)
            initial_state.update(loaded_state)
            if loaded_state.get("research_plan"):
                logger.info(
                    f"Resuming with {len(loaded_state['research_plan'])} plan categories "
                    f"and {len(loaded_state.get('search_results', []))} existing results. "
                    f"Next task: Cat {initial_state['current_category_index']}, Task {initial_state['current_task_index_in_category']}"
                )
                initial_state["topic"] = (
                    topic  # Allow overriding topic even when resuming? Or use stored topic? Let's use new one.
                )
            else:
                logger.warning(
                    f"Resume requested for {task_id}, but no previous plan found. Starting fresh."
                )

        # ═══════════════════════════════════════════════════════════════════════
        # ACTION FAST-PATH: Skip LangGraph for simple action tasks
        # ═══════════════════════════════════════════════════════════════════════
        # Detect action intent with fast keyword check (no LLM call needed)
        _action_keywords = (
            "book", "buy", "purchase", "order", "reserve", "search", "find",
            "cheapest", "best price", "ticket", "flight", "train", "hotel",
            "sign up", "signup", "register", "login", "log in", "checkout",
        )
        _research_keywords = (
            "compare", "analysis", "analyze", "research", "review",
            "what is", "how does", "explain", "summarize", "list of",
            "top 5", "top 10", "best stocks", "investment", "guide",
        )
        _topic_lower = topic.lower()
        _is_likely_action = (
            any(kw in _topic_lower for kw in _action_keywords)
            and not any(kw in _topic_lower for kw in _research_keywords)
            and not task_id  # Only for new tasks, not resume
        )

        if _is_likely_action:
            logger.info(f"[FAST-PATH] Detected simple ACTION task — bypassing LangGraph pipeline.")
            _push_to_ui_log(f"⚡ ACTION mode: {topic[:60]}")
            _push_to_ui_log("Skipping research pipeline — direct browser execution...")
            
            # Register task type
            with _STATE_LOCK:
                _TASK_TYPE_REGISTRY[self.current_task_id] = "action"

            final_state = None
            status = "unknown"
            message = None
            try:
                # Run browser task directly
                _push_to_ui_log("Starting browser agent...")
                browser_result = await run_single_browser_task(
                    task_query=topic,
                    task_id=self.current_task_id,
                    llm=self.llm,
                    browser_config=self.browser_config,
                    stop_event=self.stop_event,
                    mcp_server_config=self.mcp_server_config,
                    global_task_type="action",
                )
                
                _result_status = browser_result.get("status", "unknown")
                _result_data = browser_result.get("result") or browser_result.get("error", "No result")
                
                if _result_status == "completed":
                    status = "completed"
                    message = "Action task completed successfully."
                    _push_to_ui_log(f"✅ Task complete: {str(_result_data)[:100]}")
                else:
                    status = "error" if _result_status == "failed" else _result_status
                    message = f"Action task {_result_status}: {_result_data}"
                    _push_to_ui_log(f"⚠️ Task {_result_status}")
                
                # Save a simple report
                simple_report = f"""# Action Task Report

**Task:** {topic}

**Status:** {_result_status.upper()}

**Result:**

{_result_data}

---
*Generated via ACTION fast-path — no multi-step research pipeline used.*
"""
                _save_report_to_md(simple_report, Path(output_dir))
                _push_to_ui_log("Saving report...")
                _save_report_to_pdf(simple_report, Path(output_dir), topic=topic)
                _push_to_ui_log("✅ Report saved (PDF + Markdown)")
                
                # Create a minimal final_state for compatibility
                final_state = {
                    "task_id": self.current_task_id,
                    "topic": topic,
                    "final_report": simple_report,
                    "task_type": "action",
                }
                
            except Exception as e:
                status = "error"
                message = f"Fast-path action error: {e}"
                logger.error(message, exc_info=True)
                _push_to_ui_log(f"❌ Error: {e}")
            finally:
                logger.info(f"Fast-path execution finished for task {self.current_task_id}")
                task_id_to_clean = self.current_task_id
                self.stop_event = None
                self.current_task_id = None
                self.runner = None
                await self.close_mcp_client()
                with _STATE_LOCK:
                    _TASK_TYPE_REGISTRY.pop(task_id_to_clean, None)
                    _AGENT_STOP_FLAGS.pop(task_id_to_clean, None)
                return {
                    "status": status,
                    "message": message,
                    "task_id": task_id_to_clean,
                    "final_state": final_state if final_state else {},
                }
        # ═══════════════════════════════════════════════════════════════════════

        # --- Execute Graph using ainvoke (RESEARCH tasks only) ---
        final_state = None
        status = "unknown"
        message = None
        try:
            logger.info(f"Invoking graph execution for task {self.current_task_id}...")
            _push_to_ui_log(f"🔬 RESEARCH mode: {topic[:60]}")
            _push_to_ui_log("Building multi-step research plan...")
            # Use a generous recursion limit; default 25 is far too low for multi-task research plans
            graph_config = {"recursion_limit": 150}
            self.runner = asyncio.create_task(self.graph.ainvoke(initial_state, config=graph_config))
            final_state = await self.runner
            logger.info(f"Graph execution finished for task {self.current_task_id}.")

            # Determine status based on final state
            if self.stop_event and self.stop_event.is_set():
                status = "stopped"
                message = "Research process was stopped by request."
                logger.info(message)
            elif final_state and final_state.get("error_message"):
                status = "error"
                message = final_state["error_message"]
                logger.error(f"Graph execution completed with error: {message}")
            elif final_state and final_state.get("final_report"):
                status = "completed"
                message = "Research process completed successfully."
                logger.info(message)
            else:
                # If it ends without error/report (e.g., empty plan, stopped before synthesis)
                status = "finished_incomplete"
                message = "Research process finished, but may be incomplete (no final report generated)."
                logger.warning(message)

        except asyncio.CancelledError:
            status = "cancelled"
            message = f"Agent run task cancelled for {self.current_task_id}."
            logger.info(message)
            # final_state will remain None or the state before cancellation if checkpointing was used
        except Exception as e:
            status = "error"
            message = f"Unhandled error during graph execution for {self.current_task_id}: {e}"
            logger.error(message, exc_info=True)
            # final_state will remain None or the state before the error
        finally:
            logger.info(f"Cleaning up resources for task {self.current_task_id}")
            task_id_to_clean = self.current_task_id

            self.stop_event = None
            self.current_task_id = None
            self.runner = None  # Mark runner as finished
            await self.close_mcp_client()
            # Clean up registries with lock
            with _STATE_LOCK:
                _TASK_TYPE_REGISTRY.pop(task_id_to_clean, None)
                _AGENT_STOP_FLAGS.pop(task_id_to_clean, None)

            # Return a result dictionary including the status and the final state if available
            return {
                "status": status,
                "message": message,
                "task_id": task_id_to_clean,  # Use the stored task_id
                "final_state": final_state
                if final_state
                else {},  # Return the final state dict
            }

    async def _stop_lingering_browsers(self, task_id):
        """Attempts to stop any BrowserUseAgent instances associated with the task_id."""
        keys_to_stop = [
            key for key in _BROWSER_AGENT_INSTANCES if key.startswith(f"{task_id}_")
        ]
        if not keys_to_stop:
            return

        logger.warning(
            f"Found {len(keys_to_stop)} potentially lingering browser agents for task {task_id}. Attempting stop..."
        )
        for key in keys_to_stop:
            agent_instance = _BROWSER_AGENT_INSTANCES.get(key)
            try:
                if agent_instance:
                    # Assuming BU agent has an async stop method
                    await agent_instance.stop()
                    logger.info(f"Called stop() on browser agent instance {key}")
            except Exception as e:
                logger.error(
                    f"Error calling stop() on browser agent instance {key}: {e}"
                )

    async def stop(self):
        """Signals the currently running agent task to stop."""
        if not self.current_task_id or not self.stop_event:
            logger.info("No agent task is currently running.")
            return

        logger.info(f"Stop requested for task ID: {self.current_task_id}")
        self.stop_event.set()  # Signal the stop event
        self.stopped = True
        await self._stop_lingering_browsers(self.current_task_id)

    def close(self):
        self.stopped = False
