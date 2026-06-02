"""
Agent Marketplace Tab — Pre-built templates to get started fast.
Templates can be sent directly to the Action Agent or Deep Research tabs.
"""
import gradio as gr
from typing import Any, Dict, Optional
from src.webui.webui_manager import WebuiManager
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Template Registry
# ---------------------------------------------------------------------------

TEMPLATES = {
    "📈 Stock Market Research": {
        "description": (
            "Deep-dive research into specific stocks or market sectors. "
            "Returns P/E ratios, CAGR, dividend yield, sector analysis and investment outlook."
        ),
        "task": (
            "Research the top 5 long-term investment stocks listed on BSE/NSE India for 2025. "
            "For each stock provide: company name, sector, current P/E ratio, 5-year CAGR, "
            "dividend yield, debt-to-equity ratio, recent news, and a brief investment thesis. "
            "Conclude with a ranked recommendation table."
        ),
        "target": "deep_research",
        "category": "Finance",
    },
    "✈️ Travel Itinerary Planner": {
        "description": (
            "Generate a detailed multi-day travel plan for any destination. "
            "Includes accommodation, activities, food, transport and estimated costs."
        ),
        "task": (
            "Create a detailed 7-day travel itinerary for Switzerland (Zurich, Interlaken, Lucerne). "
            "Include: recommended hotels with price range, daily activity schedule, must-visit attractions, "
            "local food recommendations, estimated total budget per person, and transport tips. "
            "Format as a day-by-day plan."
        ),
        "target": "deep_research",
        "category": "Travel",
    },
    "🛒 Product Comparison Report": {
        "description": (
            "Compare the top products in any category. "
            "Returns feature comparison, pricing, user ratings and a clear recommendation."
        ),
        "task": (
            "Compare the top 5 laptops for software developers in 2025 under $1500. "
            "For each: model name, price, CPU/RAM/GPU specs, battery life, display quality, "
            "user rating, and pros/cons. End with a ranked recommendation table "
            "separating best value, best performance and best battery categories."
        ),
        "target": "deep_research",
        "category": "Shopping",
    },
    "📰 Daily News & Market Briefing": {
        "description": (
            "Get a concise briefing of today's top news events and their market impact."
        ),
        "task": (
            "Search the web for today's top 5 global business and financial news stories. "
            "For each story: headline, key facts, affected industries/stocks, and potential market impact. "
            "End with a one-paragraph executive summary of the overall market sentiment today."
        ),
        "target": "deep_research",
        "category": "News",
    },
    "🏢 Company Deep Dive": {
        "description": (
            "Comprehensive research on any company: business model, financials, "
            "competition, recent news and investment outlook."
        ),
        "task": (
            "Do a comprehensive deep dive on [COMPANY NAME]. Research: "
            "business model and revenue streams, latest financials (revenue, profit, growth), "
            "main competitors and market position, recent news and strategic announcements, "
            "management team quality, and a 12-month investment outlook with risks."
        ),
        "target": "deep_research",
        "category": "Finance",
    },
    "📝 Auto-Fill Form / Application": {
        "description": (
            "The agent navigates to a specific URL and fills out a form or application "
            "using the information you provide in the task. "
            "Tip: attach a PDF/DOCX with your details using the file upload in the Action Agents tab."
        ),
        "task": (
            "Navigate to [FORM URL] and fill out the application form. "
            "Use the following information:\n"
            "- Name: [YOUR NAME]\n"
            "- Email: [YOUR EMAIL]\n"
            "- Phone: [YOUR PHONE]\n"
            "Complete all required fields and submit the form. "
            "Take a screenshot of the confirmation page when done."
        ),
        "target": "browser",
        "category": "Automation",
    },
    "🔍 Web Data Extraction": {
        "description": (
            "Scrape structured data from any website. "
            "Give the agent the URL and what data to extract."
        ),
        "task": (
            "Go to [WEBSITE URL] and extract all [DATA TYPE, e.g., product names and prices]. "
            "Scroll through all pages/pagination to find all items. "
            "Return the results as a structured numbered list. "
            "If there are more than 50 items, list the first 50 and state the total count."
        ),
        "target": "browser",
        "category": "Automation",
    },
    "🤖 Research & Summarize Paper / Article": {
        "description": (
            "Search for papers, blog posts or articles on any topic and produce "
            "a structured summary with key insights."
        ),
        "task": (
            "Search the web for the top 5 most cited research papers or expert articles on [TOPIC]. "
            "For each source: title, author(s)/publication, year, key findings (3-5 bullet points), "
            "and link/URL. End with a synthesis section summarising the main consensus and open questions."
        ),
        "target": "deep_research",
        "category": "Research",
    },
    "💼 Job Market Analysis": {
        "description": (
            "Analyse job listings and salary data for any role or industry."
        ),
        "task": (
            "Research the current job market for [JOB TITLE] in [LOCATION/COUNTRY] in 2025. "
            "Include: average salary range, top hiring companies, required skills, "
            "job growth trend, remote work availability, and top job boards to apply on. "
            "Recommend 3 specific actions to improve job prospects in this field."
        ),
        "target": "deep_research",
        "category": "Career",
    },
    "🏠 Real Estate Market Research": {
        "description": (
            "Research property prices, trends and investment opportunities in any city or neighbourhood."
        ),
        "task": (
            "Research the real estate market in [CITY/AREA] for 2025. "
            "Include: average property prices (buy & rent), price trend over 3 years, "
            "best neighbourhoods for investment, upcoming infrastructure development, "
            "rental yield, and buy vs rent comparison. Conclude with an investment recommendation."
        ),
        "target": "deep_research",
        "category": "Finance",
    },
}

CATEGORY_FILTERS = ["All", "Finance", "Travel", "Shopping", "News", "Automation", "Research", "Career"]


# ---------------------------------------------------------------------------
# Tab Creation
# ---------------------------------------------------------------------------

def create_agent_marketplace_tab(webui_manager: WebuiManager):
    """Creates the Agent Marketplace tab with pre-built templates."""

    template_names = list(TEMPLATES.keys())

    with gr.Column():
        gr.Markdown(
            "### 🛒 Pick a template, customise the task, then send it to the agent of your choice.",
            elem_classes=["tab-header-text"],
        )

        with gr.Row():
            category_filter = gr.Dropdown(
                label="Filter by Category",
                choices=CATEGORY_FILTERS,
                value="All",
                interactive=True,
                scale=1,
            )
            template_dropdown = gr.Dropdown(
                label="Select Template",
                choices=template_names,
                value=template_names[0],
                interactive=True,
                scale=3,
            )

        template_description = gr.Markdown(
            value=TEMPLATES[template_names[0]]["description"]
        )

        task_preview = gr.Textbox(
            label="✏️ Task (edit before sending)",
            value=TEMPLATES[template_names[0]]["task"],
            lines=8,
            interactive=True,
        )

        template_target_badge = gr.Markdown(
            value=_get_target_badge(template_names[0])
        )

        with gr.Row():
            send_to_action_btn = gr.Button(
                "🤖 Open in Action Agents Tab",
                variant="primary",
                scale=1,
            )
            send_to_research_btn = gr.Button(
                "🔬 Open in Deep Research Tab",
                variant="secondary",
                scale=1,
            )

        status_msg = gr.Markdown(value="")

    # ── Event handlers ────────────────────────────────────────────────────────

    def _filter_templates(category: str):
        if category == "All":
            filtered = template_names
        else:
            filtered = [
                name for name, data in TEMPLATES.items()
                if data.get("category") == category
            ]
        first = filtered[0] if filtered else None
        return (
            gr.update(choices=filtered, value=first),
            TEMPLATES[first]["description"] if first else "",
            TEMPLATES[first]["task"] if first else "",
            _get_target_badge(first) if first else "",
        )

    def _on_template_select(name: Optional[str]):
        if not name or name not in TEMPLATES:
            return "", "", ""
        t = TEMPLATES[name]
        return t["description"], t["task"], _get_target_badge(name)

    def _send_to_action(task_text: str):
        """Copy task to the Action Agents user_input component."""
        user_input_comp = webui_manager.id_to_component.get("browser_use_agent.user_input")
        if user_input_comp is None:
            return (
                gr.update(),
                "⚠️ Could not find the Action Agents tab component. Please restart.",
            )
        return (
            gr.update(value=task_text),
            "✅ Task sent to **Action Agents** tab — navigate there and click ▶️ Submit Task.",
        )

    def _send_to_research(task_text: str):
        """Copy task to the Deep Research research_task component."""
        research_task_comp = webui_manager.id_to_component.get("deep_research_agent.research_task")
        if research_task_comp is None:
            return (
                gr.update(),
                "⚠️ Could not find the Deep Research tab component. Please restart.",
            )
        return (
            gr.update(value=task_text),
            "✅ Task sent to **Deep MultiAgents → Deep Research** tab — navigate there and click ▶️ Run.",
        )

    # Connect filter
    category_filter.change(
        fn=_filter_templates,
        inputs=[category_filter],
        outputs=[template_dropdown, template_description, task_preview, template_target_badge],
    )

    # Connect template selection
    template_dropdown.change(
        fn=_on_template_select,
        inputs=[template_dropdown],
        outputs=[template_description, task_preview, template_target_badge],
    )

    # Get the cross-tab output components lazily (they're registered by now)
    def _get_action_input():
        return webui_manager.id_to_component.get("browser_use_agent.user_input")

    def _get_research_input():
        return webui_manager.id_to_component.get("deep_research_agent.research_task")

    send_to_action_btn.click(
        fn=_send_to_action,
        inputs=[task_preview],
        outputs=[
            webui_manager.id_to_component.get("browser_use_agent.user_input", gr.Textbox()),
            status_msg,
        ],
    )

    send_to_research_btn.click(
        fn=_send_to_research,
        inputs=[task_preview],
        outputs=[
            webui_manager.id_to_component.get("deep_research_agent.research_task", gr.Textbox()),
            status_msg,
        ],
    )


def _get_target_badge(template_name: Optional[str]) -> str:
    if not template_name or template_name not in TEMPLATES:
        return ""
    target = TEMPLATES[template_name].get("target", "deep_research")
    category = TEMPLATES[template_name].get("category", "")
    if target == "browser":
        return (
            f"**Recommended tab:** 🤖 Action Agents &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"**Category:** {category}"
        )
    return (
        f"**Recommended tab:** 🔬 Deep Research &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"**Category:** {category}"
    )
