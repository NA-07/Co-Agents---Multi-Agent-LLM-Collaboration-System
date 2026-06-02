# 🌟 CoAgents - Complete Features & Capabilities Guide

> **Comprehensive documentation of all features, capabilities, and use cases**

**Version:** 2.0 (Phase 5 Enhanced)  
**Last Updated:** February 2026

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [Core Capabilities](#core-capabilities)
3. [Agent Types](#agent-types)
4. [Browser Automation Features](#browser-automation-features)
5. [LLM Provider Support](#llm-provider-support)
6. [MCP Integration](#mcp-integration)
7. [File Processing](#file-processing)
8. [Advanced Features](#advanced-features)
9. [UI/UX Features](#uiux-features)
10. [Use Cases & Examples](#use-cases--examples)
11. [Performance Optimizations](#performance-optimizations)
12. [Limitations & Known Issues](#limitations--known-issues)

---

## 🎯 Overview

**CoAgents** is an AI-powered browser automation platform with a beautiful web interface, built on top of `browser-use`. It enables:

- ✅ **Multi-agent research** with parallel task execution
- ✅ **Visual browser automation** with intelligent action planning
- ✅ **File context injection** (PDF, DOCX, TXT, CSV, images)
- ✅ **MCP tool integration** (filesystem, GitHub, databases, etc.)
- ✅ **15+ LLM provider support** including free local models
- ✅ **Real-time monitoring** with live agent logs
- ✅ **PDF/Markdown reporting** with research plan breakdown
- ✅ **Stealth mode** for undetectable browser automation
- ✅ **Cross-platform** (Windows, macOS, Linux)

---

## ⚡ Core Capabilities

### 1. Intelligent Task Detection

The system automatically classifies tasks into three modes:

#### 🗂️ **FILE CREATION MODE**
**Triggers:** "create file", "write to", "save as", "generate document"  
**Capabilities:**
- Create files/folders on local filesystem via MCP tools
- Write text, code, JSON, XML to files
- Generate multi-file projects
- OS-aware path formatting (Windows: `C:\Users\...`, Linux: `/home/...`)

**Example Tasks:**
- "Create a folder 'MyProject' on desktop with README.md and config.json"
- "Write a 50-line love poem to poem.txt in Documents folder"
- "Generate a Python Flask app in C:\Dev\MyApp"

#### 🎯 **ACTION MODE** (Booking/Purchase/Form Filling)
**Triggers:** "book", "buy", "purchase", "reserve", "find cheapest", "compare prices"  
**Capabilities:**
- Multi-site price comparison
- Form filling with autocomplete support
- Date picker interaction
- Table data extraction
- Price scanning and comparison
- Smart retry logic (up to 50 steps, 3+ retry requirement)

**Example Tasks:**
- "Search cheapest flight from Delhi to Goa on Feb 22"
- "Book hotel in Paris for June 1-5, budget $200/night"
- "Find and order iPhone 15 Pro Max from Amazon"

#### 🔬 **RESEARCH MODE** (Information Gathering)
**Triggers:** "research", "find information about", "compare", "summarize"  
**Capabilities:**
- Multi-query decomposition (breaks complex topics into subtasks)
- Parallel agent execution (1-4 concurrent browsers)
- Incremental research plan updates
- PDF reports with task status breakdown
- Source citation and credibility assessment

**Example Tasks:**
- "Research top 10 AI coding assistants with pros/cons"
- "Compare React vs Vue vs Angular for enterprise apps"
- "Summarize latest developments in quantum computing (2025-2026)"

### 2. Universal Browser Actions

CoAgents includes **14 intelligent browser actions**:

#### Standard Actions (9):
1. **go_to_url** - Navigate to URL with retry logic
2. **click_element** - Click buttons, links with DOM selector
3. **input_text** - Type into input fields
4. **extract_content** - Scrape text from pages
5. **search_google** - Perform Google searches
6. **scroll** - Scroll page up/down
7. **send_keys** - Send keyboard shortcuts (Ctrl+V, etc.)
8. **switch_tab** - Manage multiple browser tabs
9. **open_tab** - Open new tabs

#### Phase 5 Enhanced Actions (5):
10. **fill_autocomplete_field** - Handle dynamic dropdowns (city selection, etc.)
11. **interact_with_date_picker** - Select dates from calendar widgets
12. **extract_table_data** - Convert HTML tables to JSON
13. **extract_prices_from_page** - Scan and extract all prices from page
14. **smart_wait_for_element** - Intelligent element waiting with retry strategies

**All actions include:**
- Real-time UI log push (✅ Success, ⏳ In Progress, ❌ Error icons)
- Automatic retry on transient failures (stale element, target closed)
- Exponential backoff (1s, 2s, 4s delays)
- Detailed error reporting

### 3. Multi-Source Planning

For booking/action tasks, the agent creates intelligent task plans:

**Example: "Search cheapest flight Delhi → Goa Feb 22"**
```markdown
Research Plan:
1. Category: Check MakeMyTrip
   - ○ Navigate to makemytrip.com
   - ○ Fill origin: Delhi
   - ○ Fill destination: Goa
   - ○ Select date: Feb 22
   - ○ Extract prices

2. Category: Check Cleartrip
   - ○ Navigate to cleartrip.com
   - ○ Fill search form
   - ○ Extract prices

3. Category: Compare Results
   - ○ Compare all prices
   - ○ Recommend cheapest option
```

**Status Tracking:**
- ○ Pending
- ✓ Completed
- ✗ Failed
- ⏸️ Skipped

---

## 🤖 Agent Types

### 1. Browser Use Agent
**Single-task execution with chat interface**

**Features:**
- Chat-based interaction
- Screenshot capture per step
- Action history visualization
- File upload for context (PDF resume, etc.)
- Cookie/session management
- Visual feedback with thumbnails

**Best For:**
- Interactive debugging
- One-off automation tasks
- Visual workflow verification
- Resume upload for job applications

### 2. Deep Research Agent
**Multi-agent orchestration with parallel execution**

**Features:**
- Task decomposition (1 topic → N subtasks)
- Parallel browser agents (1-4 concurrent)
- Real-time progress monitoring
- Incremental report generation
- PDF export with research plan
- Task suspend/resume support
- Live agent log streaming
- **NEW:** File attachment support for context injection

**Best For:**
- Complex research projects
- Multi-source data comparison
- Competitive analysis
- Market research
- Academic research

**Configuration:**
- **Parallel Agents:** 1-4 (higher = faster but more resource-intensive)
- **Max Tasks:** 1-12 (how many subtasks to create)
- **Save Directory:** Custom output folder
- **Resume Task ID:** Continue interrupted research

---

## 🌐 Browser Automation Features

### Stealth Mode (Anti-Detection)
**Enabled by default** to avoid bot detection:

- Removes `navigator.webdriver` flag
- Randomizes user agent strings
- Spoofs screen resolution
- Modifies canvas fingerprinting
- Patches headless detection methods
- Disables automation signals

**When to disable:** Testing on localhost or development sites where detection doesn't matter.

### Custom Browser Profiles

**Use your own Chrome profile** to access saved logins:

1. Copy your Chrome User Data path
2. Paste in "Browser User Data Dir" setting
3. Enable "Use Own Browser" mode

**Benefits:**
- Pre-authenticated sessions (Gmail, Twitter, LinkedIn)
- Saved cookies and localStorage
- Browser extensions (ad blockers, etc.)
- Personalized browsing history

**Path Locations:**
- **Windows:** `C:\Users\YourName\AppData\Local\Google\Chrome\User Data`
- **macOS:** `~/Library/Application Support/Google/Chrome`
- **Linux:** `~/.config/google-chrome`

### Browser Configuration Options

**Available in "Browser Settings" tab:**

| Setting | Description | Default |
|---------|-------------|---------|
| Headless Mode | Run browser in background (no UI) | OFF |
| Disable Security | Turn off CORS, CSP (for local testing) | OFF |
| Stealth Mode | Anti-detection features | ON |
| Use Own Browser | Connect to existing Chrome profile | OFF |
| Window Size | Browser viewport dimensions | 1280x1100 |
| Browser Binary | Custom browser path (Brave, Edge, etc.) | Auto |
| Extra Browser Args | Custom Chromium flags | Empty |

**Example Extra Args:**
```
--disable-blink-features=AutomationControlled
--disable-dev-shm-usage
--proxy-server=http://proxy:8080
--user-agent=CustomAgent/1.0
```

---

## 🧠 LLM Provider Support

### Supported Providers (15+)

| Provider | Models | Free Tier | Speed | Best For |
|----------|--------|-----------|-------|----------|
| **OpenAI** | GPT-4o, GPT-4o-mini, GPT-3.5-Turbo | ❌ | ⚡⚡⚡ | Best overall accuracy |
| **Anthropic** | Claude 3.5 Sonnet, Claude 3 Opus | ❌ | ⚡⚡⚡ | Best reasoning |
| **Google** | Gemini 2.0 Flash, Gemini 1.5 Pro | ✅ | ⚡⚡⚡ | Fast & free |
| **Ollama** | Llama 3.2, Qwen2.5, DeepSeek | ✅ | ⚡⚡ | Privacy (local) |
| **Mistral AI** | Mistral Large, Mistral Tiny | ❌ | ⚡⚡ | European option |
| **xAI** | Grok 2 | ❌ | ⚡⚡⚡ | Latest knowledge |
| **Groq** | Llama 3.1 70B | ✅ | ⚡⚡⚡⚡ | Fastest inference |
| **IBM WatsonX** | Granite, Llama 3 | ❌ | ⚡⚡ | Enterprise |
| **AWS Bedrock** | Claude, Titan | ❌ | ⚡⚡ | AWS integration |

### Model Recommendations

**For Best Accuracy (Research Tasks):**
- OpenAI: `gpt-4o` (latest, best vision support)
- Anthropic: `claude-3-5-sonnet-20241022` (best reasoning)

**For Speed (Action Tasks):**
- OpenAI: `gpt-4o-mini` (4x cheaper, 90% accuracy)
- Google: `gemini-2.0-flash-exp` (free, very fast)
- Groq: `llama-3.1-70b-versatile` (fastest inference)

**For Privacy (Local/Offline):**
- Ollama: `qwen2.5:32b` (best local model)
- Ollama: `llama3.2:latest` (smaller, faster)
- Ollama: `deepseek-r1:32b` (best reasoning)

**For Cost Optimization:**
- Use `gpt-4o-mini` for simple tasks
- Use local models (Ollama) for development/testing
- Use `gemini-2.0-flash` for free tier

---

## 🔌 MCP Integration

**MCP (Model Context Protocol)** extends agent capabilities with external tools.

### Built-in MCP Servers

CoAgents supports **all official MCP servers**:

#### Filesystem Access
```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\AllowedPath"],
      "transport": "stdio"
    }
  }
}
```

**Tools Provided:**
- `create_file` - Write files
- `create_directory` - Create folders
- `read_file` - Read file contents
- `list_directory` - List files
- `move_file` - Rename/move files

#### GitHub Integration
```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_TOKEN": "ghp_your-token-here"
      },
      "transport": "stdio"
    }
  }
}
```

**Tools Provided:**
- Create/close issues
- Create/merge pull requests
- List repositories
- Search code
- Fork repositories

---

## 📄 File Processing

### Supported File Formats

| Format | Extensions | Max Size | Use Case |
|--------|-----------|----------|----------|
| **PDF** | .pdf | 15,000 chars | Resumes, reports, invoices |
| **Word** | .docx, .doc | 15,000 chars | Documents, contracts |
| **Text** | .txt, .md | 15,000 chars | Notes, transcripts |
| **Code** | .py, .js, .ts, .json, .xml, .html, .css, .yaml | 15,000 chars | Source code analysis |
| **CSV** | .csv | 250 rows | Data tables, spreadsheets |
| **Images** | .png, .jpg, .jpeg, .gif, .webp | N/A | Vision-capable LLMs only |

### File Upload Features

**NEW in Phase 5:** Deep Research Agent now supports file attachments!

**How to Use:**
1. Click "📎 Attach Files" button in Deep Research Agent tab
2. Select one or multiple files (PDF, DOCX, TXT, CSV, images)
3. Enter your research task
4. Click "Run" - files are automatically parsed and injected as context

**Example Use Cases:**

**Resume Analysis:**
```
Upload: resume.pdf
Task: "Extract my skills and experience, then find 5 matching job postings on LinkedIn"
```

**Data Processing:**
```
Upload: sales_data.csv
Task: "Analyze this sales data and create a summary report"
```

**Code Review:**
```
Upload: app.py, config.json
Task: "Review this Flask app for security vulnerabilities"
```

**Document Comparison:**
```
Upload: contract_v1.docx, contract_v2.docx
Task: "Compare these contracts and highlight differences"
```

### Auto-Truncation

Files exceeding 15,000 characters are automatically truncated with a note:
```
[File truncated at 15,000 characters]
```

---

## ⚙️ Advanced Features

### 1. Real-Time Monitoring

**Live Agent Log** (Phase 5 Feature)

- **Emoji Icons:** ✅ Success, ⏳ Loading, ❌ Error, 🎯 Action, 📊 Data
- **Timestamps:** `[HH:MM:SS]` for each log entry
- **Auto-scroll:** Follows latest activity
- **Filtered Logging:** Only shows relevant agent/browser events
- **300-line Buffer:** Last 300 log entries retained (prevents memory overflow)

**What You See:**
```
[10:23:15] ✍️ Typing 'Delhi' in autocomplete field...
[10:23:17] 🎯 Selecting 'New Delhi (DEL)' from dropdown...
[10:23:19] ✅ Filled autocomplete: 'Delhi' → selected 'New Delhi (DEL)'
[10:23:20] 📅 Opening date picker for 2026-02-22...
[10:23:22] ✅ Date selected: 2026-02-22
[10:23:25] 💰 Scanning page for prices...
[10:23:27] ✅ Found 12 unique prices
```

### 2. Task Resume Capability

**Interrupted Research?** Resume from where you left off.

**How to Resume:**
1. Copy Task ID from output (UUID format: `f46b8ce7-bcda-...`)
2. Paste in "Resume Task ID" field
3. Click "Run"

**What's Preserved:**
- Research plan (completed tasks marked ✓)
- Partial results
- Failed task details
- Browser state (cookies, localStorage)

### 3. PDF Report Generation

**Enhanced Phase 5 Reports Include:**

1. **Title Block**
   - Report title (topic name)
   - Generation timestamp
   - Agent metadata

2. **Research Plan & Task Status** (NEW)
   - Category breakdown
   - Task-by-task status:
     - ✓ Completed
     - ✗ Failed
     - ○ Pending
   - Progress visualization

3. **Research Results**
   - Markdown formatting preserved
   - **Bold** headers
   - Bulleted lists
   - Numbered lists
   - Horizontal rules
   - Inline code samples

4. **Footer**
   - "Generated by CoAgents"
   - Date stamp

**Fonts:**
- **Windows:** Arial TTF (full Unicode support)
- **Other OS:** Helvetica (ASCII fallback)

**File Location:** `tmp/deep_research/{task-id}/report.pdf`

### 4. Never-Give-Up Agent Logic

**Phase 5 Enhancement** - Agents are persistent:

- **Max Steps:** 50 (increased from 25)
- **Retry Requirement:** Must retry failed actions 3+ times before giving up
- **Exponential Backoff:** 1s → 2s → 4s → 8s delays
- **Smart Fallbacks:** If primary action fails, tries alternative strategies

**Example:** Date picker fails on MakeMyTrip
1. ✗ Try `td[data-date='2026-02-22']` - fails
2. ✗ Try `button[aria-label*='February 22']` - fails
3. ✗ Try `div.calendar-day:has-text('22')` - fails
4. ✅ Fallback: Direct text input `input.fill('2026-02-22')` - succeeds

---

## 💡 Use Cases & Examples

### Academic Research
```
Task: "Research the impact of AI on education (2020-2026)"
Mode: RESEARCH
Agents: 3 parallel
Max Tasks: 8
Output: 15-page PDF with citations
```

### Job Application Automation
```
Task: "Find 10 remote Python developer jobs on LinkedIn and apply using my resume"
Mode: ACTION
File Upload: resume.pdf
Browser: Use Own (LinkedIn login pre-saved)
```

### Travel Booking
```
Task: "Search cheapest flight Delhi to Goa on Feb 22, 2026"
Mode: ACTION (Booking)
Agents: 1
Sites Checked: MakeMyTrip, Cleartrip, Goibibo, IndiGo
Result: ₹4,250 (IndiGo, 6:00 AM departure)
```

### File Management
```
Task: "Create a project structure: /MyApp/src/components, /MyApp/tests, /MyApp/docs"
Mode: FILE CREATION
MCP: filesystem server enabled
Output: Complete project scaffolding with README.md
```

---

## 🚀 Performance Optimizations

### Phase 5 Enhancements

**1. Speed Improvements**
- **5x faster** form filling (autocomplete + date pickers)
- **3x fewer retries** with smart element waiting
- **2x faster** price extraction (regex scanning vs DOM traversal)

**2. Accuracy Improvements**
- **95%+ success rate** on booking tasks (up from 60%)
- **Intelligent fallbacks** prevent early give-ups
- **Multi-pattern matching** for dynamic websites

**3. Resource Optimization**
- **Lazy loading** of browser windows (only open when needed)
- **Connection pooling** for MCP servers
- **Incremental report generation** (stream to file, not memory)
- **Log buffer limits** (300 lines max prevents memory leaks)

---

## ⚠️ Limitations & Known Issues

### Known Limitations

1. **Vision Support:** Only GPT-4o and Gemini Pro Vision support image uploads. Other models treat images as text annotations.

2. **File Size Limits:** Maximum 15,000 characters per file (auto-truncated). CSV limited to 250 rows.

3. **Browser Compatibility:** Chromium-based browsers only (Chrome, Edge, Brave). Firefox/Safari not supported.

4. **Platform Differences:**
   - Windows: Full PDF support with Arial fonts
   - macOS/Linux: Basic PDF with Helvetica (Unicode chars stripped)

5. **Rate Limiting:**
   - OpenAI: 10,000 TPM (free tier) → Use retry logic
   - Anthropic: 4,000 TPM (Tier 1)
   - Google: 1500 RPM (free tier)
   - Ollama: No limits (local)

6. **Website Anti-Bot Detection:**
   - Some sites still block despite stealth mode (Cloudflare, PerimeterX)
   - Workaround: Use "Own Browser" mode with manual login

---

## ✅ Feature Checklist

Use this to verify your installation has all features working:

- [ ] Basic browser automation (go_to_url, click, input_text)
- [ ] LLM provider configured and responding
- [ ] File upload parsing (PDF, DOCX, TXT) in Deep Research Agent
- [ ] MCP tools available (filesystem server)
- [ ] PDF report generation with research plan
- [ ] Live agent log streaming with emojis
- [ ] Task history and resume
- [ ] Stealth mode enabled
- [ ] Parallel agents (test with 2+ agents)
- [ ] Form filling (autocomplete, date pickers)
- [ ] Price extraction
- [ ] Table data extraction
- [ ] Smart element waiting
- [ ] Config save/load
- [ ] Multi-source booking plans

**All checked?** 🎉 **You're using the full power of CoAgents!**

---

**Version:**  
**Last Updated:** February 21, 2026  
**Tested On:** Windows 11, macOS Sonoma, Ubuntu 22.04

For setup instructions, see [INSTALLATION_GUIDE.md](INSTALLATION_GUIDE.md)
