# CoAgents UI Documentation

This document provides a detailed breakdown of every component, setting, and button present in the CoAgents Web UI. It explains what each element does and how you can use it to configure, control, and monitor your AI-powered multi-agents.

---

## 1. Main Interface (`src/webui/interface.py`)

The main interface is the overarching wrapper built with Gradio that contains several tabs. It acts as the navigation hub for all the settings and functionalities of CoAgents.

### UI Elements:
- **Main Header:** Displays "Introducing CoAgents" along with a subheading "Control your browser with AI-powered Multiagents."
- **Tabs Container:** It organizes the UI into logical sections:
  - `⚙️ Agent Settings`
  - `🌐 Browser Settings`
  - `🤖 Action Agents`
  - `🎁 Deep MultiAgents`
  - `📁 Load & Save Config`

---

## 2. Agent Settings Tab (`src/webui/components/agent_settings_tab.py`)

This tab configures the behavior, intelligence, and boundaries of the AI agents.

### Custom Prompts
- **Override system prompt (Textbox):** Completely replaces the default system prompt of the agent. Use this when you want the agent to adopt a completely different persona, role, or base set of instructions.
- **Extend system prompt (Textbox):** Appends additional instructions to the **end** of the default system prompt. Use this to add specific rules or constraints without overriding the base prompt (e.g., "Always speak in Spanish" or "Never click on external links").

### Model Context Protocol (MCP) Server
- **MCP server json (File Upload):** Allows you to upload a model context protocol `.json` file that provides the agent with new capabilities or custom tools.
- **MCP server (Hidden Textbox):** Internally stores the loaded JSON configuration text.
- **MCP Status (Hidden Markdown):** Displays whether the tool integration loaded successfully or failed.

### LLM Configuration
This section defines the "Brain" of the autonomous agent.
- **LLM Provider (Dropdown):** Choose which AI vendor you want to use (e.g., `openai`, `anthropic`, `ollama`, `google`).
- **LLM Model Name (Dropdown / Text):** Choose the specific tier of the model (e.g., `gpt-4o`, `claude-3-5-sonnet`). You can also manually type a custom model string.
- **LLM Temperature (Slider):** Controls the creativity of the agent (0.0 to 2.0). A lower value makes the agent more deterministic and literal; a higher value allows for more experimental reasoning.
- **Use Vision (Checkbox):** Toggles whether the agent can "see" website screenshots. Essential for complex visual navigation.
- **Ollama Context Length (Slider):** (Visible for local Ollama deployments). Limits how much memory history the model can retain per call.
- **Base URL & API Key (Textboxes):** Input fields to override default `.env` API parameters. Useful for utilizing proxy endpoints.

### Planner LLM Configuration
Sometimes it's cheaper/faster to use one model to make a "plan" and another to execute it. This section mirrors the exact fields from above (Provider, Model, Temperature, Vision, API config) specifically for the "Planning" agent which oversees the "Action" agent.

### Agent Execution Limits
Safety rails for the agent to prevent runaway loops and runaway token billing.
- **Max Run Steps (Slider):** The absolute maximum number of API calls/loops the agent can make for a single task. Once reached, the agent stops.
- **Max Number of Actions (Slider):** Caps how many atomic actions (like clicking or typing) the agent can perform in a *single* step.
- **Max Input Tokens (Number):** Restricts the maximum size of the contextual prompt given to the API to save costs.
- **Tool Calling Method (Dropdown):** Determines the structured output protocol (e.g., `auto`, `json_mode`, `function_calling`).

---

## 3. Browser Settings Tab (`src/webui/components/browser_settings_tab.py`)

This tab dictates how Playwright interacts with the local or remote web browser.

### Path Configurations
- **Browser Binary Path (Textbox):** A file path to a specific Chromium/Chrome installation on your local machine if you do not want to use the default Playwright binary.
- **Browser User Data Dir (Textbox):** Maps the agent to an existing user profile containing existing cookies, cached logins, and saved passwords.

### Execution Modes
- **Use Own Browser (Checkbox):** Forces the agent to attach to your active, already-opened browser instance instead of spinning up an isolated container.
- **Keep Browser Open (Checkbox):** Prevents the agent from closing the browser window when its task ends. Good for debugging.
- **Headless Mode (Checkbox):** Runs the browser silently without launching a graphical window at all. Useful for executing tasks in the background quickly.
- **Disable Security (Checkbox):** Bypasses standard web security policies (like CORS). Highly useful for automation but lowers local security.
- **Stealth Mode (Checkbox):** Spoofs the browser fingerprints (masks navigator variables, WebGL) to evade basic bot-detection techniques on sensitive websites.

### UI Geometry & Debugging
- **Window Width / Window Height (Numbers):** Defines the fixed screen resolution size of the automated browser window.
- **CDP URL / WSS URL (Textboxes):** Connection strings to hook into a remotely hosted remote-browser container instead of a local one.
- **Recording Path / Trace Path (Textboxes):** Local directory path to dump a screen recording `.gif` or a Playwright debugging `.trace` to analyze failures after the fact.
- **Save Directory for browser downloads (Textbox):** Set where files that the agent decides to download are saved.

---

## 4. Action Agents Tab (`src/webui/components/browser_use_agent_tab.py`)

The primary window used for general web automation and simple tasks.

- **Agent Mode (Radio buttons):** Switches execution mode. Determines whether the agent acts strictly as a web-browser automation tool or purely as a conversational Chatbot.
- **Chatbot Window:** The large central visual element. Displays the history of user commands, agent responses, visual screenshots, and internal reasoning steps.
- **User Input (Textbox):** Where you tell the agent what to do (e.g., "Find the cheapest flight to Tokyo").
- **Uploaded Files (File Upload):** Attach a PDF, CSV, or Image to give the agent context right at the task's start.
- **Run (Button):** Submits your instructions and starts the background execution loop.
- **Stop (Button):** Interrupts execution. Immediately stops the current API request or playwright script.
- **Pause/Resume (Button):** Freezes the current execution loop temporarily.
- **Clear Chat (Button):** Deletes the visual history from the display window (does not impact actual background backend state unless reset).
- **Agent History JSON & Recording (Outputs):** At the end of a successful run, these components show the telemetry dumps and video playback of what exactly the agent did.

---

## 5. Deep MultiAgents (Deep Research) Tab (`src/webui/components/deep_research_agent_tab.py`)

A dedicated space for `DeepResearchAgent` to manage vastly complex tasks requiring recursive planning, sub-agents, and extensive information gathering.

- **Task History Dropdown & Load Task:** Instead of rerunning long queries, you can reload past JSON reports by selecting them from a dropdown menu.
- **Research Task (Textbox):** The main input command indicating the deep research goal.
- **File Upload:** Upload reference text that the research planner uses as base knowledge.

### Parallel Settings
- **Max Parallel Agents (Number):** Scales up how many overlapping tasks you want to run simultaneously. Setting this higher allows faster multi-browser execution.
- **Max Tasks (Slider):** Puts a ceiling on how many parallel branches or categories the planner agent is allowed to build for a single goal.
- **Save Directory (Textbox):** Where the resulting `.md` reports or plans are stored.

### Outputs
- **Task Type Display (Label):** Shows if the agent is in "Research Mode" (reading) or "Action Mode" (purchasing/booking).
- **Live Log (Textbox):** High-speed stream representing real-time system logs. Good for monitoring the status of parallel agents.
- **Markdown Display:** Live rendering of the dynamically updated plan, usually `research_plan.md` or `report.md`.
- **Download PDF/MD Report:** Triggers when a test finishes, giving you standard formats for the final researched response.

---

## 6. Load & Save Config Tab (`src/webui/components/load_save_config_tab.py`)

Panel for persisting and retrieving complex configurations.

- **Upload saved config (File):** If you backed up your configuration, upload it here to restore it.
- **Load Config (Button):** Updates all sliders, dropdowns, prompts, and tokens in the UI according to the uploaded configuration file.
- **Save UI Settings (Button):** Takes the current snapshot of every single UI element on every single tab, and exports it to a `.json` file that you can save.
- **Status (Textbox):** Indicates whether loading or saving the configuration was successful.

---

