import gradio as gr
from gradio.components import Component

from src.webui.webui_manager import WebuiManager
from src.utils import config


def create_load_save_config_tab(webui_manager: WebuiManager):
    """
    Creates a load and save config tab.

    Save Config  — serialises every UI widget value to a timestamped JSON file
                   in ./tmp/webui_settings/.
    Load Config  — uploads a previously-saved JSON and restores all widget values.
    """
    tab_components = {}

    gr.Markdown(
        """
        ### 💾 Load & Save UI Configuration
        **Save** — takes a snapshot of all current settings (LLM provider, model, temperature,
        API keys, browser options, …) and writes them to `./tmp/webui_settings/<timestamp>.json`.

        **Load** — upload a previously saved `.json` file and click **Load Config** to restore
        every setting exactly as it was saved.
        """
    )

    config_file = gr.File(
        label="Upload saved config (.json)",
        file_types=[".json"],
        interactive=True,
    )
    with gr.Row():
        load_config_button = gr.Button("📂 Load Config", variant="primary")
        save_config_button = gr.Button("💾 Save UI Settings", variant="primary")

    config_status = gr.Textbox(
        label="Status",
        lines=2,
        interactive=False,
        placeholder="Status messages will appear here…",
    )

    tab_components.update(dict(
        load_config_button=load_config_button,
        save_config_button=save_config_button,
        config_status=config_status,
        config_file=config_file,
    ))

    webui_manager.add_components("load_save_config", tab_components)

    # ------------------------------------------------------------------ #
    #  Wire up Save Config                                                 #
    #  inputs  = all current component VALUES (positional args)           #
    #  outputs = just the status textbox                                  #
    # ------------------------------------------------------------------ #
    save_config_button.click(
        fn=webui_manager.save_config,
        inputs=list(webui_manager.get_components()),   # ordered list → positional values
        outputs=[config_status],
    )

    # ------------------------------------------------------------------ #
    #  Wire up Load Config                                                 #
    #  inputs  = the uploaded file component                              #
    #  outputs = ALL components registered so far (to restore values)     #
    #            The generator in load_config yields gr.update() for each #
    # ------------------------------------------------------------------ #
    load_config_button.click(
        fn=webui_manager.load_config,
        inputs=[config_file],
        outputs=webui_manager.get_components(),        # all registered components
    )

