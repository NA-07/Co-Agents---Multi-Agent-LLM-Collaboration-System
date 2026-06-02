import json
import os

import gradio as gr
from gradio.components import Component
from typing import Any, Dict, Optional
from src.webui.webui_manager import WebuiManager
from src.utils import config
import logging
from functools import partial

logger = logging.getLogger(__name__)


def _get_default_provider() -> str:
    """
    Pick the best available LLM provider based on .env API keys.
    Priority: DEFAULT_LLM env var > first key found in .env > ollama (free fallback)
    """
    forced = os.getenv("DEFAULT_LLM", "").strip()
    if forced and forced in config.model_names:
        return forced
    # Auto-detect from available API keys (ordered by popularity)
    key_to_provider = [
        ("GOOGLE_API_KEY", "google"),
        ("OPENAI_API_KEY", "openai"),
        ("ANTHROPIC_API_KEY", "anthropic"),
        ("DEEPSEEK_API_KEY", "deepseek"),
        ("MISTRAL_API_KEY", "mistral"),
        ("GROK_API_KEY", "grok"),
        ("ALIBABA_API_KEY", "alibaba"),
    ]
    for env_var, provider in key_to_provider:
        if os.getenv(env_var, "").strip():
            logger.info(f"Auto-selected provider '{provider}' based on {env_var} in .env")
            return provider
    # No key found — fall back to free local Ollama
    return "ollama"


def update_model_dropdown(llm_provider):
    """
    Update the model name dropdown with predefined models for the selected provider.
    """
    # Use predefined models for the selected provider
    if llm_provider in config.model_names:
        return gr.Dropdown(choices=config.model_names[llm_provider], value=config.model_names[llm_provider][0],
                           interactive=True)
    else:
        return gr.Dropdown(choices=[], value="", interactive=True, allow_custom_value=True)


async def update_mcp_server(mcp_file, webui_manager: WebuiManager):
    """
    Load an MCP JSON file, sanitise it (strip 'comment' and other non-spec
    fields), and store the clean JSON in the config textbox.

    mcp_file may be:
      - a plain file-path string  (older Gradio)
      - a dict {"path": ..., "orig_name": ...}  (Gradio 5.x)
      - None / empty
    """
    if hasattr(webui_manager, "bu_controller") and webui_manager.bu_controller:
        logger.warning("⚠️ Close controller because mcp file has changed!")
        await webui_manager.bu_controller.close_mcp_client()
        webui_manager.bu_controller = None

    # --- Resolve the actual file path from whatever Gradio passes us ---
    resolved_path = None
    if isinstance(mcp_file, dict):
        resolved_path = mcp_file.get("path") or mcp_file.get("name")
    elif isinstance(mcp_file, str) and mcp_file:
        resolved_path = mcp_file

    if not resolved_path or not os.path.exists(str(resolved_path)) or not str(resolved_path).endswith('.json'):
        logger.warning(f"{resolved_path} is not a valid MCP file.")
        return None, gr.update(visible=False)

    with open(resolved_path, 'r', encoding='utf-8') as f:
        mcp_server = json.load(f)

    # Sanitise: strip comment / non-spec fields before storing
    _ALLOWED = {"command", "args", "env", "transport", "url", "headers"}
    servers = mcp_server.get("mcpServers", mcp_server)
    clean = {}
    for name, cfg in servers.items():
        clean[name] = {k: v for k, v in cfg.items() if k in _ALLOWED}
    mcp_server_clean = {"mcpServers": clean}

    logger.info(f"MCP config loaded: {len(clean)} server(s): {list(clean.keys())}")
    return json.dumps(mcp_server_clean, indent=2), gr.update(visible=True)


def create_agent_settings_tab(webui_manager: WebuiManager):
    """
    Creates an agent settings tab.
    """
    input_components = set(webui_manager.get_components())
    tab_components = {}

    with gr.Group():
        with gr.Column():
            override_system_prompt = gr.Textbox(label="Override system prompt", lines=4, interactive=True)
            extend_system_prompt = gr.Textbox(label="Extend system prompt", lines=4, interactive=True)

    with gr.Group():
        mcp_json_file = gr.File(label="MCP server json", interactive=True, file_types=[".json"])
        mcp_server_config = gr.Textbox(label="MCP server", lines=6, interactive=True, visible=False)
        mcp_status = gr.Markdown(
            value="",
            visible=False,
            elem_id="mcp_status",
        )

    _default_provider = _get_default_provider()
    _default_model = config.model_names.get(_default_provider, ["qwen2.5:7b"])[0]

    with gr.Group():
        with gr.Row():
            llm_provider = gr.Dropdown(
                choices=[provider for provider, model in config.model_names.items()],
                label="LLM Provider",
                value=_default_provider,
                info="Select LLM provider for LLM",
                interactive=True
            )
            llm_model_name = gr.Dropdown(
                label="LLM Model Name",
                choices=config.model_names.get(_default_provider, ["qwen2.5:7b"]),
                value=_default_model,
                interactive=True,
                allow_custom_value=True,
                info="Select a model in the dropdown options or directly type a custom model name"
            )
        with gr.Row():
            llm_temperature = gr.Slider(
                minimum=0.0,
                maximum=2.0,
                value=0.6,
                step=0.1,
                label="LLM Temperature",
                info="Controls randomness in model outputs",
                interactive=True
            )

            use_vision = gr.Checkbox(
                label="Use Vision",
                value=(_default_provider != "ollama"),
                info="Enable Vision (send screenshots to LLM). Disable for non-VL Ollama models like qwen2.5:7b — use qwen2.5-vl:7b for vision.",
                interactive=True
            )

            ollama_num_ctx = gr.Slider(
                minimum=2 ** 8,
                maximum=2 ** 16,
                value=8192,
                step=1,
                label="Ollama Context Length",
                info="Max context tokens for Ollama. 8192 recommended for 7b/14b models (less = faster, fewer errors).",
                visible=(_default_provider == "ollama"),
                interactive=True
            )

        with gr.Row():
            llm_base_url = gr.Textbox(
                label="Base URL",
                value="",
                info="API endpoint URL (if required)"
            )
            llm_api_key = gr.Textbox(
                label="API Key",
                type="password",
                value="",
                info="Your API key (leave blank to use .env)"
            )

    with gr.Group():
        with gr.Row():
            planner_llm_provider = gr.Dropdown(
                choices=[provider for provider, model in config.model_names.items()],
                label="Planner LLM Provider (Optional)",
                info="Leave empty for faster single-model operation. Only set this if you want a separate planning model.",
                value=None,
                interactive=True
            )
            planner_llm_model_name = gr.Dropdown(
                label="Planner LLM Model Name",
                interactive=True,
                allow_custom_value=True,
                info="Select a model in the dropdown options or directly type a custom model name"
            )
        with gr.Row():
            planner_llm_temperature = gr.Slider(
                minimum=0.0,
                maximum=2.0,
                value=0.6,
                step=0.1,
                label="Planner LLM Temperature",
                info="Controls randomness in model outputs",
                interactive=True
            )

            planner_use_vision = gr.Checkbox(
                label="Use Vision(Planner LLM)",
                value=False,
                info="Enable Vision(Input highlighted screenshot into LLM)",
                interactive=True
            )

            planner_ollama_num_ctx = gr.Slider(
                minimum=2 ** 8,
                maximum=2 ** 16,
                value=16000,
                step=1,
                label="Ollama Context Length",
                info="Controls max context length model needs to handle (less = faster)",
                visible=False,
                interactive=True
            )

        with gr.Row():
            planner_llm_base_url = gr.Textbox(
                label="Base URL",
                value="",
                info="API endpoint URL (if required)"
            )
            planner_llm_api_key = gr.Textbox(
                label="API Key",
                type="password",
                value="",
                info="Your API key (leave blank to use .env)"
            )

    with gr.Row():
        max_steps = gr.Slider(
            minimum=1,
            maximum=200,
            value=25,
            step=1,
            label="Max Run Steps",
            info="Maximum number of steps the agent will take (lower = fewer API calls)",
            interactive=True
        )
        max_actions = gr.Slider(
            minimum=1,
            maximum=100,
            value=10,
            step=1,
            label="Max Number of Actions",
            info="Maximum actions agent takes per step",
            interactive=True
        )

    with gr.Row():
        max_input_tokens = gr.Number(
            label="Max Input Tokens",
            value=8192 if _default_provider == "ollama" else 128000,
            precision=0,
            info="Max tokens sent to LLM per step",
            interactive=True
        )
        tool_calling_method = gr.Dropdown(
            label="Tool Calling Method",
            value="auto",
            interactive=True,
            allow_custom_value=True,
            choices=['function_calling', 'json_mode', 'raw', 'auto', 'tools', "None"],
            visible=True
        )
    tab_components.update(dict(
        override_system_prompt=override_system_prompt,
        extend_system_prompt=extend_system_prompt,
        llm_provider=llm_provider,
        llm_model_name=llm_model_name,
        llm_temperature=llm_temperature,
        use_vision=use_vision,
        ollama_num_ctx=ollama_num_ctx,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        planner_llm_provider=planner_llm_provider,
        planner_llm_model_name=planner_llm_model_name,
        planner_llm_temperature=planner_llm_temperature,
        planner_use_vision=planner_use_vision,
        planner_ollama_num_ctx=planner_ollama_num_ctx,
        planner_llm_base_url=planner_llm_base_url,
        planner_llm_api_key=planner_llm_api_key,
        max_steps=max_steps,
        max_actions=max_actions,
        max_input_tokens=max_input_tokens,
        tool_calling_method=tool_calling_method,
        mcp_json_file=mcp_json_file,
        mcp_server_config=mcp_server_config,
        mcp_status=mcp_status,
    ))
    webui_manager.add_components("agent_settings", tab_components)

    def _on_provider_change(provider):
        """Auto-adjust defaults when switching providers."""
        is_ollama = (provider == "ollama")
        # Ollama: disable vision (non-VL models), reduce token limits
        # Cloud APIs: enable vision, restore full token limit
        return (
            gr.update(visible=is_ollama),            # ollama_num_ctx visibility
            gr.update(value=not is_ollama),           # use_vision
            gr.update(value=8192 if is_ollama else 128000),  # max_input_tokens
        )

    llm_provider.change(
        fn=_on_provider_change,
        inputs=[llm_provider],
        outputs=[ollama_num_ctx, use_vision, max_input_tokens]
    )
    llm_provider.change(
        lambda provider: update_model_dropdown(provider),
        inputs=[llm_provider],
        outputs=[llm_model_name]
    )
    planner_llm_provider.change(
        fn=lambda x: gr.update(visible=x == "ollama"),
        inputs=[planner_llm_provider],
        outputs=[planner_ollama_num_ctx]
    )
    planner_llm_provider.change(
        lambda provider: update_model_dropdown(provider),
        inputs=[planner_llm_provider],
        outputs=[planner_llm_model_name]
    )

    async def update_wrapper(mcp_file):
        """Wrapper that loads MCP config and shows connection status."""
        result = await update_mcp_server(mcp_file, webui_manager)
        if result is not None:
            config_text, _visibility = result
            if config_text:
                try:
                    parsed = json.loads(config_text)
                    servers = parsed.get("mcpServers", {})
                    server_names = list(servers.keys())
                    status_msg = (
                        f"### ✅ MCP Config Loaded\n"
                        f"**{len(server_names)} server(s)** detected: {', '.join(server_names)}\n\n"
                        f"MCP tools will connect when you submit a task."
                    )
                except Exception:
                    status_msg = "### ⚠️ MCP config loaded but could not parse server list."
                yield {
                    mcp_server_config: config_text,
                    mcp_status: gr.update(value=status_msg, visible=True),
                }
            else:
                yield {
                    mcp_server_config: gr.update(value=None, visible=False),
                    mcp_status: gr.update(value="### ❌ No valid servers found in MCP config.", visible=True),
                }
        else:
            yield {
                mcp_server_config: gr.update(value=None, visible=False),
                mcp_status: gr.update(value="", visible=False),
            }

    mcp_json_file.change(
        update_wrapper,
        inputs=[mcp_json_file],
        outputs=[mcp_server_config, mcp_status]
    )
