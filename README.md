# Co-Agents---Multi-Agent-LLM-Collaboration-System
# 🤖 CoAgents WebUI

> AI-powered browser automation with a beautiful web interface.


## Quick Start

### 1. Setup guide

```powershell
# Create virtual environment
python -m venv .venv

# Activate it
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Install browser automation
python -m playwright install chromium
```

### 2. Configure `.env`

```powershell
copy .env.example .env
```

Edit `.env` and add at least one API key, or use Ollama (see below).

### 3. Run

```powershell
# Option A: Activate venv + run (recommended)
.\.venv\Scripts\Activate.ps1
python start_webui.py

# Option B: Double-click
run.bat
```

Open **http://127.0.0.1:7788** in your browser.

---

## Using Ollama (Free Local Models)

Ollama lets you run LLMs locally. No API keys, no cost, fully private.

### Step 1 — Install Ollama

Download from **https://ollama.com/download** and run the installer.

### Step 2 — Pull a model

Open a terminal and run:

| Purpose | Command | VRAM needed |
|---------|---------|-------------|
| **Quick testing** | `ollama pull qwen2.5:7b` | ~5 GB |
| **Better quality** | `ollama pull qwen2.5:14b` | ~10 GB |
| **Best quality** | `ollama pull qwen2.5:32b` | ~20 GB |
| **Reasoning/Research** | `ollama pull deepseek-r1:14b` | ~10 GB |
| **Coding tasks** | `ollama pull qwen2.5-coder:14b` | ~10 GB |
| **Lightweight** | `ollama pull llama2:7b` | ~5 GB |

### Step 3 — Configure in the WebUI

1. Open http://127.0.0.1:7788
2. Go to **⚙️ Agent Settings**
3. Set:
   - **LLM Provider** → `ollama`
   - **LLM Model Name** → `qwen2.5:7b` (or whatever you pulled)
   - **Base URL** → `http://localhost:11434` (default)
   - **Temperature** → `0.6`
   - **Use Vision** → ✅ checked
4. Go to **🤖 Run Agent** and enter a task.

### Using Ollama for Deep Research

1. In **⚙️ Agent Settings**, configure Ollama as above
2. Go to **🎁 Agent Marketplace** → **Deep Research**
3. Enter a research topic
4. The agent will plan, search the web, and generate a report

### Using Ollama for the Planner LLM

You can use a separate (cheaper/faster) model as the planner:

1. In **⚙️ Agent Settings** scroll to the **Planner LLM** section
2. Set **Planner LLM Provider** → `ollama`
3. Set **Planner Model** → `qwen2.5:7b` (use a smaller model for planning)
4. Keep the main LLM as a larger model for execution

### Verify Ollama is running

```powershell
curl http://localhost:11434/api/tags
```

If you see a JSON response listing your models, Ollama is ready.

---

## `.env` Configuration Reference

```env
# ═══════════════════════════════════════════════════
# LLM API Keys — fill in the ones you want to use
# ═══════════════════════════════════════════════════

# OpenAI (gpt-4o, gpt-3.5-turbo)
OPENAI_ENDPOINT=https://api.openai.com/v1
OPENAI_API_KEY=sk-...

# Anthropic (claude-3.5-sonnet)
ANTHROPIC_ENDPOINT=https://api.anthropic.com
ANTHROPIC_API_KEY=sk-ant-...

# Google Gemini (gemini-2.0-flash) — free tier available
GOOGLE_API_KEY=AIza...

# DeepSeek (cheapest paid option ~$0.14/M tokens)
DEEPSEEK_ENDPOINT=https://api.deepseek.com
DEEPSEEK_API_KEY=sk-...

# Ollama (free local) — no API key needed
OLLAMA_ENDPOINT=http://localhost:11434

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_API_VERSION=2025-01-01-preview

# Grok (X.AI)
GROK_ENDPOINT=https://api.x.ai/v1
GROK_API_KEY=

# Mistral
MISTRAL_ENDPOINT=https://api.mistral.ai/v1
MISTRAL_API_KEY=

# ═══════════════════════════════════════════════════
# Default LLM — set to "ollama" for free local models
# ═══════════════════════════════════════════════════
DEFAULT_LLM=ollama

# ═══════════════════════════════════════════════════
# Browser settings
# ═══════════════════════════════════════════════════
BROWSER_PATH=
BROWSER_USER_DATA=
KEEP_BROWSER_OPEN=true
USE_OWN_BROWSER=false
```

### Which API key should I use?

| Provider | Cost | Best for |
|----------|------|----------|
| **Ollama** | Free (local) | Testing, privacy, no internet needed |
| **Google Gemini** | Free tier available | Vision tasks, general use |
| **DeepSeek** | ~$0.14/M tokens | Cheapest paid, good quality |
| **OpenAI** | ~$2.50/M tokens | Most reliable, best quality |
| **Anthropic** | ~$3/M tokens | Complex reasoning |

---

## Commands Reference

```powershell
# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Start the server (auto port detection)
python start_webui.py

# Start on a specific port
python start_webui.py --port 8080

# Start with a different theme
python start_webui.py --theme Soft

# Check if server is running
netstat -ano | findstr :7788

# Stop the server (replace PID with actual process ID)
taskkill /PID <PID> /F
```

---

## Project Structure

```
CoAgents WebUI/
├── .env                  # Your API keys (not committed to git)
├── .env.example          # Template for .env
├── requirements.txt      # Python dependencies
├── start_webui.py        # Main entry point (auto port)
├── webui.py              # Original entry point
├── run.bat               # Double-click to start (Windows)
├── src/
│   ├── agent/
│   │   ├── browser_use/  # Browser automation agent
│   │   └── deep_research/# Deep research agent
│   ├── browser/          # Custom browser management
│   ├── controller/       # Action controller + MCP
│   ├── utils/            # Config, LLM providers, MCP client
│   └── webui/            # Gradio UI components
└── tests/                # Test scripts
```

---

## License

See [LICENSE](LICENSE) for details.
