import json
import os
import gradio as gr
from datetime import datetime
from typing import Optional, Dict, List
import asyncio
import time

from gradio.components import Component
from browser_use.agent.service import Agent
from src.browser.custom_browser import CustomBrowser
from src.browser.custom_context import CustomBrowserContext
from src.controller.custom_controller import CustomController
from src.agent.deep_research.deep_research_agent import DeepResearchAgent


class WebuiManager:
    def __init__(self, settings_save_dir: str = "./tmp/webui_settings"):
        self.id_to_component: dict[str, Component] = {}
        self.component_to_id: dict[Component, str] = {}

        self.settings_save_dir = settings_save_dir
        os.makedirs(self.settings_save_dir, exist_ok=True)

    def init_browser_use_agent(self) -> None:
        """
        init browser use agent
        """
        self.bu_agent: Optional[Agent] = None
        self.bu_browser: Optional[CustomBrowser] = None
        self.bu_browser_context: Optional[CustomBrowserContext] = None
        self.bu_controller: Optional[CustomController] = None
        self.bu_chat_history: List[Dict[str, Optional[str]]] = []
        self.bu_response_event: Optional[asyncio.Event] = None
        self.bu_user_help_response: Optional[str] = None
        self.bu_current_task: Optional[asyncio.Task] = None
        self.bu_agent_task_id: Optional[str] = None

    def init_deep_research_agent(self) -> None:
        """
        init deep research agent
        """
        self.dr_agent: Optional[DeepResearchAgent] = None
        self.dr_current_task = None
        self.dr_task_id: Optional[str] = None
        self.dr_save_dir: Optional[str] = None
        self.dr_llm_key: Optional[str] = None  # tracks provider::model for change detection

    def add_components(self, tab_name: str, components_dict: dict[str, "Component"]) -> None:
        """
        Add tab components
        """
        for comp_name, component in components_dict.items():
            comp_id = f"{tab_name}.{comp_name}"
            self.id_to_component[comp_id] = component
            self.component_to_id[component] = comp_id

    def get_components(self) -> list["Component"]:
        """
        Get all components
        """
        return list(self.id_to_component.values())

    def get_component_by_id(self, comp_id: str) -> "Component":
        """
        Get component by id
        """
        return self.id_to_component[comp_id]

    def get_id_by_component(self, comp: "Component") -> str:
        """
        Get id by component
        """
        return self.component_to_id[comp]

    def save_config(self, *args) -> str:
        """
        Save all current UI component values to a timestamped JSON file.
        Gradio passes component values as positional arguments when inputs is a list/set.
        We reconstruct the {comp: value} mapping using the ordered component list.
        """
        all_components = list(self.id_to_component.values())
        # Pair each component with its current value from positional args
        components_dict = {}
        for i, comp in enumerate(all_components):
            if i < len(args):
                components_dict[comp] = args[i]

        cur_settings = {}
        for comp, val in components_dict.items():
            # Skip buttons, file-pickers, and non-interactive components
            if isinstance(comp, (gr.Button, gr.File)):
                continue
            if str(getattr(comp, "interactive", True)).lower() == "false":
                continue
            try:
                comp_id = self.get_id_by_component(comp)
                # Only save JSON-serialisable values
                json.dumps(val)          # test serialisability
                cur_settings[comp_id] = val
            except (TypeError, ValueError):
                pass  # skip non-serialisable values (e.g. file objects)

        config_name = datetime.now().strftime("%Y%m%d-%H%M%S")
        save_path = os.path.join(self.settings_save_dir, f"{config_name}.json")
        with open(save_path, "w", encoding="utf-8") as fw:
            json.dump(cur_settings, fw, indent=4)

        return f"✅ Config saved: {save_path}"

    def load_config(self, config_path: Optional[str]):
        """
        Load config from a JSON file and yield gr.update() dicts for all stored components.
        config_path may be either a plain file-path string or a Gradio file-info dict
        (which contains the key "path" or "name") depending on the Gradio version.
        """
        config_status = self.id_to_component["load_save_config.config_status"]

        # --- Resolve actual file path ---
        resolved_path: Optional[str] = None
        if isinstance(config_path, dict):
            # Gradio ≥ 4 passes {"path": ..., "orig_name": ...} for gr.File uploads
            resolved_path = config_path.get("path") or config_path.get("name")
        elif isinstance(config_path, str) and config_path:
            resolved_path = config_path

        if not resolved_path:
            yield {config_status: gr.update(value="❌ Error: Please upload a configuration file first")}
            return

        if not os.path.exists(resolved_path):
            yield {config_status: gr.update(value=f"❌ Error: File not found: {resolved_path}")}
            return

        try:
            with open(resolved_path, "r", encoding="utf-8") as fr:
                ui_settings = json.load(fr)
        except json.JSONDecodeError as e:
            yield {config_status: gr.update(value=f"❌ Error: Invalid JSON file: {str(e)}")}
            return
        except (IOError, OSError) as e:
            yield {config_status: gr.update(value=f"❌ Error loading file: {str(e)}")}
            return

        # Validate that the loaded JSON is a dict (not a list or other type)
        if not isinstance(ui_settings, dict):
            yield {config_status: gr.update(
                value="❌ Error: Config file must contain a JSON object (dict), "
                      f"not {type(ui_settings).__name__}. "
                      "Please upload a file saved via 'Save UI Settings'."
            )}
            return

        update_components: dict = {}
        for comp_id, comp_val in ui_settings.items():
            if comp_id not in self.id_to_component:
                continue
            comp = self.id_to_component[comp_id]
            # Skip buttons and file-pickers — they have no meaningful saved value
            if isinstance(comp, (gr.Button, gr.File)):
                continue
            if comp.__class__.__name__ == "Chatbot":
                update_components[comp] = gr.update(value=comp_val, type="messages")
            else:
                update_components[comp] = gr.update(value=comp_val)

            # Yield early after setting llm_provider so the model-dropdown
            # change-callback can fire before we set planner_llm_provider.
            if comp_id == "agent_settings.llm_provider":
                yield update_components.copy()
                time.sleep(0.15)

        update_components[config_status] = gr.update(
            value=f"✅ Loaded config: {os.path.basename(resolved_path)}"
        )
        yield update_components
