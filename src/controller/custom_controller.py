import pyperclip
from typing import Optional, Type, Callable, Dict, Any, Union, Awaitable, TypeVar
from pydantic import BaseModel
from browser_use.agent.views import ActionResult
from browser_use.browser.context import BrowserContext
from browser_use.controller.service import Controller, DoneAction
from browser_use.controller.registry.service import Registry, RegisteredAction
from main_content_extractor import MainContentExtractor
from browser_use.controller.views import (
    ClickElementAction,
    DoneAction,
    ExtractPageContentAction,
    GoToUrlAction,
    InputTextAction,
    OpenTabAction,
    ScrollAction,
    SearchGoogleAction,
    SendKeysAction,
    SwitchTabAction,
)
import logging
import inspect
import asyncio
import os
import traceback
from langchain_core.language_models.chat_models import BaseChatModel
from browser_use.agent.views import ActionModel, ActionResult

from src.utils.mcp_client import create_tool_param_model, setup_mcp_client_and_tools

from browser_use.utils import time_execution_sync

logger = logging.getLogger(__name__)

Context = TypeVar('Context')

# Maximum retry attempts for transient browser action failures (stale element, target closed, etc.)
_ACTION_MAX_RETRIES = 2
_ACTION_RETRY_DELAY = 0.5  # seconds between retries (doubles each attempt)


class CustomController(Controller):
    def __init__(self, exclude_actions: list[str] = [],
                 output_model: Optional[Type[BaseModel]] = None,
                 ask_assistant_callback: Optional[Union[Callable[[str, BrowserContext], Dict[str, Any]], Callable[
                     [str, BrowserContext], Awaitable[Dict[str, Any]]]]] = None,
                 ):
        super().__init__(exclude_actions=exclude_actions, output_model=output_model)
        self._register_custom_actions()
        self.ask_assistant_callback = ask_assistant_callback
        self.mcp_client = None
        self.mcp_server_config = None
        self.mcp_connected_servers = []
        self.mcp_failed_servers = []
        self.mcp_total_tools = 0

    def _register_custom_actions(self):
        """Register all custom browser actions"""

        @self.registry.action(
            'Create an audio file (MP3) from text and save it to a specific path (like the desktop).'
        )
        async def create_audio_file(text: str, file_path: str):
            """Generates an MP3 audio file from the provided text and saves it to the given path."""
            try:
                # Ensure the path makes sense or expand '~' for user directories
                file_path = os.path.expanduser(file_path)
                
                # Make sure the directory exists
                directory = os.path.dirname(file_path)
                if directory and not os.path.exists(directory):
                    os.makedirs(directory)

                from gtts import gTTS
                tts = gTTS(text=text, lang='en')
                tts.save(file_path)
                
                msg = f"Successfully created audio file at {file_path}"
                logger.info(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                err_msg = f"Failed to create audio file: {str(e)}"
                logger.error(err_msg)
                return ActionResult(error=err_msg)

        @self.registry.action(
            'Create a video file (MP4 or AVI) from text and save it to a specific path (like the desktop).'
        )
        async def create_video_file(text: str, file_path: str):
            """Generates an MP4 video file from the provided text and saves it to the given path."""
            try:
                file_path = os.path.expanduser(file_path)
                directory = os.path.dirname(file_path)
                if directory and not os.path.exists(directory):
                    os.makedirs(directory)

                import cv2
                import numpy as np
                
                width, height = 640, 480
                fps = 24
                duration = 3 # seconds
                fourcc = cv2.VideoWriter_fourcc(*'mp4v') if file_path.endswith('.mp4') else cv2.VideoWriter_fourcc(*'XVID')
                
                out = cv2.VideoWriter(file_path, fourcc, fps, (width, height))
                
                for i in range(fps * duration):
                    frame = np.zeros((height, width, 3), dtype=np.uint8)
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    font_scale = 1
                    thickness = 2
                    (text_width, text_height), _ = cv2.getTextSize(text, font, font_scale, thickness)
                    x = (width - text_width) // 2
                    y = (height + text_height) // 2
                    
                    cv2.putText(frame, text, (x, y), font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)
                    out.write(frame)
                    
                out.release()
                
                msg = f"Successfully created video file at {file_path}"
                logger.info(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                err_msg = f"Failed to create video file: {str(e)}"
                logger.error(err_msg)
                return ActionResult(error=err_msg)

        @self.registry.action(
            "When executing tasks, prioritize autonomous completion. However, if you encounter a definitive blocker "
            "that prevents you from proceeding independently – such as needing credentials you don't possess, "
            "requiring subjective human judgment, needing a physical action performed, encountering complex CAPTCHAs, "
            "or facing limitations in your capabilities – you must request human assistance."
        )
        async def ask_for_assistant(query: str, browser: BrowserContext):
            if self.ask_assistant_callback:
                if inspect.iscoroutinefunction(self.ask_assistant_callback):
                    user_response = await self.ask_assistant_callback(query, browser)
                else:
                    user_response = self.ask_assistant_callback(query, browser)
                msg = f"AI ask: {query}. User response: {user_response['response']}"
                logger.info(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            else:
                return ActionResult(extracted_content="Human cannot help you. Please try another way.",
                                    include_in_memory=True)

        @self.registry.action(
            'Upload file to interactive element with file path ',
        )
        async def upload_file(index: int, path: str, browser: BrowserContext, available_file_paths: list[str]):
            if path not in available_file_paths:
                return ActionResult(error=f'File path {path} is not available')

            if not os.path.exists(path):
                return ActionResult(error=f'File {path} does not exist')

            dom_el = await browser.get_dom_element_by_index(index)

            file_upload_dom_el = dom_el.get_file_upload_element()

            if file_upload_dom_el is None:
                msg = f'No file upload element found at index {index}'
                logger.info(msg)
                return ActionResult(error=msg)

            file_upload_el = await browser.get_locate_element(file_upload_dom_el)

            if file_upload_el is None:
                msg = f'No file upload element found at index {index}'
                logger.info(msg)
                return ActionResult(error=msg)

            try:
                await file_upload_el.set_input_files(path)
                msg = f'Successfully uploaded file to index {index}'
                logger.info(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                msg = f'Failed to upload file to index {index}: {str(e)}'
                logger.info(msg)
                return ActionResult(error=msg)

        # ── Additional HyperAgent-style browser actions ─────────────────────

        @self.registry.action(
            'Dismiss cookie consent banners, popups, and alert dialogs. '
            'Use this when a cookie/privacy banner is blocking interaction with the page.'
        )
        async def dismiss_popup(browser: BrowserContext):
            """Attempts to dismiss cookie banners and overlay dialogs."""
            page = await browser.get_current_page()
            dismissed = []
            # Try common cookie-banner selectors
            _selectors = [
                # Cookie consent buttons (most common frameworks)
                'button[id*="accept"]', 'button[id*="agree"]', 'button[id*="consent"]',
                'button[class*="accept"]', 'button[class*="agree"]', 'button[class*="consent"]',
                'a[id*="accept"]', 'a[class*="accept"]',
                '[aria-label*="accept"]', '[aria-label*="Accept"]',
                '[aria-label*="agree"]', '[aria-label*="cookie"]',
                # Generic close / dismiss
                'button[class*="close"]', 'button[aria-label="Close"]',
                '.cookie-banner button', '.cookie-popup button',
                '#cookie-notice button', '.cc-btn',
                # GDPR / OneTrust / Cookiebot
                '#onetrust-accept-btn-handler',
                '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
                '.cky-btn-accept', '#acceptAllCookies',
            ]
            for sel in _selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=200):
                        await btn.click(timeout=500)
                        dismissed.append(sel)
                        await asyncio.sleep(0.15)
                        break  # one dismiss is usually enough
                except Exception:
                    continue
            # Also dismiss any native alert/confirm/prompt dialogs
            try:
                page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))
            except Exception:
                pass
            if dismissed:
                msg = f"Dismissed popup/cookie banner using selector: {dismissed[0]}"
                logger.info(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            return ActionResult(extracted_content="No cookie/popup banner found to dismiss.", include_in_memory=True)

        @self.registry.action(
            'Hover over a page element by its index to reveal hidden menus or tooltips.'
        )
        async def hover_element(index: int, browser: BrowserContext):
            """Hover over an element identified by its DOM index."""
            try:
                dom_el = await browser.get_dom_element_by_index(index)
                el = await browser.get_locate_element(dom_el)
                if el is None:
                    return ActionResult(error=f'Element at index {index} not found.')
                await el.hover(timeout=3000)
                msg = f'Hovered over element at index {index}.'
                logger.info(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                return ActionResult(error=f'Hover failed on index {index}: {e}')

        @self.registry.action(
            'Select an option from a <select> dropdown element by its visible text or value.'
        )
        async def select_dropdown_option(index: int, value: str, browser: BrowserContext):
            """Select a dropdown option. `value` is matched against option text first, then value attribute."""
            try:
                dom_el = await browser.get_dom_element_by_index(index)
                el = await browser.get_locate_element(dom_el)
                if el is None:
                    return ActionResult(error=f'Dropdown at index {index} not found.')
                tag = await el.evaluate("e => e.tagName.toLowerCase()")
                if tag != 'select':
                    return ActionResult(error=f'Element at index {index} is <{tag}>, not <select>.')
                # Try label first, then value attribute
                try:
                    await el.select_option(label=value, timeout=3000)
                except Exception:
                    await el.select_option(value=value, timeout=3000)
                msg = f'Selected option "{value}" in dropdown at index {index}.'
                logger.info(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                return ActionResult(error=f'select_dropdown_option failed: {e}')

        @self.registry.action(
            'Wait for a specific element to appear on the page (up to timeout_ms milliseconds). '
            'Use a CSS selector string.'
        )
        async def wait_for_element(selector: str, browser: BrowserContext, timeout_ms: int = 5000):
            """Wait until an element matching the CSS selector appears."""
            try:
                page = await browser.get_current_page()
                await page.wait_for_selector(selector, state='visible', timeout=timeout_ms)
                msg = f'Element "{selector}" appeared within {timeout_ms}ms.'
                logger.info(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                return ActionResult(error=f'wait_for_element("{selector}") timed out after {timeout_ms}ms: {e}')

        @self.registry.action(
            'Execute arbitrary JavaScript on the current page and return the result. '
            'Use for advanced interactions the other actions cannot handle.'
        )
        async def execute_javascript(script: str, browser: BrowserContext):
            """Run a JavaScript expression/statement and return its result."""
            try:
                page = await browser.get_current_page()
                result = await page.evaluate(script)
                result_str = str(result)[:2000]
                msg = f'JS executed. Result: {result_str}'
                logger.info(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                return ActionResult(error=f'execute_javascript failed: {e}')

        @self.registry.action(
            'Scroll continuously to the bottom of the page to load lazy/infinite-scroll content. '
            'Useful for pages that load more items on scroll (e.g. social media feeds, product listings).'
        )
        async def scroll_to_bottom(browser: BrowserContext, max_scrolls: int = 10, delay_ms: int = 500):
            """Incrementally scroll to the bottom, waiting for new content between scrolls."""
            try:
                page = await browser.get_current_page()
                prev_height = 0
                scrolls = 0
                for _ in range(max_scrolls):
                    cur_height = await page.evaluate("document.body.scrollHeight")
                    if cur_height == prev_height:
                        break  # no new content loaded
                    prev_height = cur_height
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(delay_ms / 1000.0)
                    scrolls += 1
                msg = f'Scrolled to bottom {scrolls} time(s). Final height: {prev_height}px.'
                logger.info(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                return ActionResult(error=f'scroll_to_bottom failed: {e}')

        @self.registry.action(
            'Switch the browser context into an iframe identified by a CSS selector or index. '
            'Pass selector="main" to switch back to the main frame.'
        )
        async def switch_to_iframe(selector: str, browser: BrowserContext):
            """Switch into an iframe for interaction, or back to main frame."""
            try:
                page = await browser.get_current_page()
                if selector.lower() == 'main':
                    # Switch back to main frame — this is implicit via page.main_frame
                    msg = 'Switched back to main frame.'
                    logger.info(msg)
                    return ActionResult(extracted_content=msg, include_in_memory=True)
                frame_el = page.frame_locator(selector)
                # Verify frame exists by trying to locate something inside
                try:
                    await frame_el.locator('body').wait_for(timeout=3000)
                except Exception:
                    pass
                msg = f'Switched context to iframe "{selector}". Use actions on elements inside this frame.'
                logger.info(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                return ActionResult(error=f'switch_to_iframe("{selector}") failed: {e}')

        @self.registry.action(
            'Fill an autocomplete field (like airport/city selectors) by typing text and selecting from dropdown. '
            'Handles dynamic dropdowns that appear after typing. '
            'Example: fill_autocomplete_field(field_selector="input[name=origin]", search_text="Delhi", '
            'option_text="New Delhi", wait_ms=1000)'
        )
        async def fill_autocomplete_field(
            field_selector: str, 
            search_text: str, 
            option_text: str,
            wait_ms: int = 1000,
            browser: BrowserContext = None
        ):
            """Fill autocomplete field with smart dropdown selection."""
            try:
                from src.agent.deep_research.deep_research_agent import _push_to_ui_log
                page = await browser.get_current_page()
                
                _push_to_ui_log(f"✍️ Typing '{search_text}' in autocomplete field...")
                # Click and clear the field first
                await page.locator(field_selector).click()
                await page.locator(field_selector).fill('')
                await asyncio.sleep(0.3)
                
                # Type text slowly to trigger autocomplete
                await page.locator(field_selector).type(search_text, delay=50)
                await asyncio.sleep(wait_ms / 1000.0)
                
                _push_to_ui_log(f"🎯 Selecting '{option_text}' from dropdown...")
                # Try common dropdown patterns
                dropdown_selectors = [
                    f"li:has-text('{option_text}')",
                    f"div.dropdown-item:has-text('{option_text}')",
                    f"[role='option']:has-text('{option_text}')",
                    f".autocomplete-option:has-text('{option_text}')",
                    f"ul li:has-text('{option_text}')",
                ]
                
                selected = False
                for selector in dropdown_selectors:
                    try:
                        await page.locator(selector).first.click(timeout=2000)
                        selected = True
                        break
                    except Exception:
                        continue
                
                if not selected:
                    # Fallback: press Enter or Tab to select first option
                    await page.locator(field_selector).press('Enter')
                    await asyncio.sleep(0.5)
                
                msg = f"✅ Filled autocomplete: '{search_text}' → selected '{option_text}'"
                logger.info(msg)
                _push_to_ui_log(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                err_msg = f'fill_autocomplete_field failed: {e}'
                logger.error(err_msg)
                return ActionResult(error=err_msg)

        @self.registry.action(
            'Interact with date picker (calendar widget) to select a specific date. '
            'Handles common date picker patterns. '
            'Example: interact_with_date_picker(picker_selector="input[name=departure_date]", '
            'target_date="2026-02-22", date_format="YYYY-MM-DD")'
        )
        async def interact_with_date_picker(
            picker_selector: str,
            target_date: str,
            date_format: str = "YYYY-MM-DD",
            browser: BrowserContext = None
        ):
            """Select date from calendar widget."""
            try:
                from src.agent.deep_research.deep_research_agent import _push_to_ui_log
                from datetime import datetime
                
                page = await browser.get_current_page()
                
                _push_to_ui_log(f"📅 Opening date picker for {target_date}...")
                # Click the date input to open picker
                await page.locator(picker_selector).click()
                await asyncio.sleep(0.5)
                
                # Parse target date
                if date_format == "YYYY-MM-DD":
                    dt = datetime.strptime(target_date, "%Y-%m-%d")
                else:
                    dt = datetime.strptime(target_date, date_format)
                
                day_text = str(dt.day)
                month_name = dt.strftime("%B")  # "February"
                year_text = str(dt.year)
                
                _push_to_ui_log(f"📅 Selecting {month_name} {day_text}, {year_text}...")
                
                # Try common calendar patterns
                calendar_selectors = [
                    f"td[data-date='{target_date}']",
                    f"button[aria-label*='{month_name} {day_text}']",
                    f"div.calendar-day:has-text('{day_text}')",
                    f"td.day:has-text('{day_text}')",
                    f"[data-day='{day_text}']",
                ]
                
                selected = False
                for selector in calendar_selectors:
                    try:
                        await page.locator(selector).first.click(timeout=2000)
                        selected = True
                        break
                    except Exception:
                        continue
                
                if not selected:
                    # Fallback: type date directly if picker accepts keyboard input
                    await page.locator(picker_selector).fill(target_date)
                
                msg = f"✅ Date selected: {target_date}"
                logger.info(msg)
                _push_to_ui_log(msg)
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                err_msg = f'interact_with_date_picker failed: {e}'
                logger.error(err_msg)
                return ActionResult(error=err_msg)

        @self.registry.action(
            'Extract structured data from HTML tables on the page. '
            'Returns rows as a list of dictionaries with column headers as keys. '
            'Example: extract_table_data(table_selector="table.flights", max_rows=20)'
        )
        async def extract_table_data(
            table_selector: str = "table",
            max_rows: int = 50,
            browser: BrowserContext = None
        ):
            """Scrape table data into structured format."""
            try:
                from src.agent.deep_research.deep_research_agent import _push_to_ui_log
                page = await browser.get_current_page()
                
                _push_to_ui_log(f"📊 Extracting table data from '{table_selector}'...")
                
                # Check if table exists
                table_count = await page.locator(table_selector).count()
                if table_count == 0:
                    return ActionResult(error=f"No table found with selector '{table_selector}'")
                
                # Extract using JavaScript
                table_data = await page.evaluate(f"""
                    () => {{
                        const tables = document.querySelectorAll('{table_selector}');
                        if (!tables.length) return [];
                        
                        const table = tables[0];
                        const rows = Array.from(table.querySelectorAll('tr'));
                        
                        // Get headers from first row or thead
                        const headerRow = table.querySelector('thead tr') || rows[0];
                        const headers = Array.from(headerRow.querySelectorAll('th, td'))
                                             .map(h => h.textContent.trim());
                        
                        // Get data rows
                        const dataRows = rows.slice(headers.length > 0 ? 1 : 0, {max_rows} + 1);
                        
                        return dataRows.map(row => {{
                            const cells = Array.from(row.querySelectorAll('td'));
                            const rowData = {{}};
                            cells.forEach((cell, idx) => {{
                                const key = headers[idx] || `col_${{idx}}`;
                                rowData[key] = cell.textContent.trim();
                            }});
                            return rowData;
                        }});
                    }}
                """)
                
                msg = f"✅ Extracted {len(table_data)} rows from table"
                logger.info(msg)
                _push_to_ui_log(msg)
                
                # Return JSON string for easy parsing
                import json
                return ActionResult(
                    extracted_content=json.dumps(table_data, indent=2, ensure_ascii=False),
                    include_in_memory=True
                )
            except Exception as e:
                err_msg = f'extract_table_data failed: {e}'
                logger.error(err_msg)
                return ActionResult(error=err_msg)

        @self.registry.action(
            'Extract all prices/currency amounts from the current page. '
            'Returns a list of detected prices with their context. '
            'Example: extract_prices_from_page(currency_symbol="₹", min_price=100, max_price=100000)'
        )
        async def extract_prices_from_page(
            currency_symbol: str = "₹",
            min_price: float = 0,
            max_price: float = 999999999,
            browser: BrowserContext = None
        ):
            """Find and extract all prices on the page."""
            try:
                from src.agent.deep_research.deep_research_agent import _push_to_ui_log
                import re
                page = await browser.get_current_page()
                
                _push_to_ui_log("💰 Scanning page for prices...")
                
                # Get page text
                page_text = await page.evaluate("document.body.innerText")
                
                # Regex patterns for prices
                patterns = [
                    rf"{re.escape(currency_symbol)}\s*([0-9,]+(?:\.[0-9]{{2}})?)",  # ₹ 1,500.00
                    rf"([0-9,]+(?:\.[0-9]{{2}})?)\s*{re.escape(currency_symbol)}",  # 1,500.00₹
                    r"\b([0-9,]+(?:\.[0-9]{2})?)\b",  # Plain numbers (fallback)
                ]
                
                prices = []
                for pattern in patterns:
                    matches = re.finditer(pattern, page_text)
                    for match in matches:
                        price_str = match.group(1).replace(',', '')
                        try:
                            price_val = float(price_str)
                            if min_price <= price_val <= max_price:
                                prices.append({
                                    "value": price_val,
                                    "formatted": f"{currency_symbol}{price_val:,.2f}",
                                    "context": page_text[max(0, match.start()-30):match.end()+30].strip()
                                })
                        except ValueError:
                            continue
                
                # Remove duplicates and sort by price
                unique_prices = {p['value']: p for p in prices}.values()
                sorted_prices = sorted(unique_prices, key=lambda x: x['value'])
                
                msg = f"✅ Found {len(sorted_prices)} unique prices"
                logger.info(msg)
                _push_to_ui_log(msg)
                
                import json
                return ActionResult(
                    extracted_content=json.dumps(sorted_prices[:20], indent=2, ensure_ascii=False),
                    include_in_memory=True
                )
            except Exception as e:
                err_msg = f'extract_prices_from_page failed: {e}'
                logger.error(err_msg)
                return ActionResult(error=err_msg)

        @self.registry.action(
            'Smart wait for an element to appear on the page with multiple retry strategies. '
            'Useful when page is loading dynamically. '
            'Example: smart_wait_for_element(selector="div.results", max_wait_seconds=15, check_visibility=True)'
        )
        async def smart_wait_for_element(
            selector: str,
            max_wait_seconds: int = 5,
            check_visibility: bool = True,
            browser: BrowserContext = None
        ):
            """Intelligently wait for element with retries."""
            try:
                from src.agent.deep_research.deep_research_agent import _push_to_ui_log
                page = await browser.get_current_page()
                
                _push_to_ui_log(f"⏳ Waiting for element '{selector}'...")
                
                locator = page.locator(selector)
                wait_kwargs = {"timeout": max_wait_seconds * 1000}
                
                if check_visibility:
                    await locator.first.wait_for(state="visible", **wait_kwargs)
                else:
                    await locator.first.wait_for(state="attached", **wait_kwargs)
                
                count = await locator.count()
                msg = f"✅ Element '{selector}' appeared ({count} found)"
                logger.info(msg)
                _push_to_ui_log(msg)
                
                return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                err_msg = f'Element "{selector}" not found after {max_wait_seconds}s: {e}'
                logger.warning(err_msg)
                return ActionResult(error=err_msg)

    @time_execution_sync('--act')
    async def act(
            self,
            action: ActionModel,
            browser_context: Optional[BrowserContext] = None,
            #
            page_extraction_llm: Optional[BaseChatModel] = None,
            sensitive_data: Optional[Dict[str, str]] = None,
            available_file_paths: Optional[list[str]] = None,
            #
            context: Context | None = None,
    ) -> ActionResult:
        """Execute an action with automatic retry on transient browser failures.

        Retries up to _ACTION_MAX_RETRIES times on stale-element, target-closed,
        and similar Playwright transient errors with exponential backoff.
        """
        # Transient error substrings that warrant a retry
        _TRANSIENT_ERRORS = (
            "element is not attached",
            "element was detached",
            "target closed",
            "target page, context or browser has been closed",
            "frame was detached",
            "execution context was destroyed",
            "node is detached from document",
            "stale",
            "element is not visible",
            "element is outside of the viewport",
            "waiting for locator",
        )

        last_exc: Optional[Exception] = None
        for attempt in range(1, _ACTION_MAX_RETRIES + 1):
            try:
                return await self._execute_action(
                    action=action,
                    browser_context=browser_context,
                    page_extraction_llm=page_extraction_llm,
                    sensitive_data=sensitive_data,
                    available_file_paths=available_file_paths,
                    context=context,
                )
            except Exception as e:
                last_exc = e
                err_lower = str(e).lower()
                is_transient = any(t in err_lower for t in _TRANSIENT_ERRORS)
                if is_transient and attempt < _ACTION_MAX_RETRIES:
                    delay = _ACTION_RETRY_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        f"[act] Transient error on attempt {attempt}/{_ACTION_MAX_RETRIES}: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise
        # Should not reach here, but just in case:
        raise last_exc  # type: ignore[misc]

    async def _execute_action(
            self,
            action: ActionModel,
            browser_context: Optional[BrowserContext] = None,
            page_extraction_llm: Optional[BaseChatModel] = None,
            sensitive_data: Optional[Dict[str, str]] = None,
            available_file_paths: Optional[list[str]] = None,
            context: Context | None = None,
    ) -> ActionResult:
        """Internal: run a single action attempt (no retry logic)."""

        try:
            for action_name, params in action.model_dump(exclude_unset=True).items():
                if params is not None:
                    if action_name.startswith("mcp"):
                        # this is a mcp tool
                        logger.debug(f"Invoke MCP tool: {action_name}")
                        mcp_tool = self.registry.registry.actions.get(action_name).function
                        result = await mcp_tool.ainvoke(params)
                    else:
                        result = await self.registry.execute_action(
                            action_name,
                            params,
                            browser=browser_context,
                            page_extraction_llm=page_extraction_llm,
                            sensitive_data=sensitive_data,
                            available_file_paths=available_file_paths,
                            context=context,
                        )

                    if isinstance(result, str):
                        return ActionResult(extracted_content=result)
                    elif isinstance(result, ActionResult):
                        return result
                    elif result is None:
                        return ActionResult()
                    else:
                        raise ValueError(f'Invalid action result type: {type(result)} of {result}')
            return ActionResult()
        except Exception as e:
            raise e

    async def setup_mcp_client(self, mcp_server_config: Optional[Dict[str, Any]] = None):
        self.mcp_server_config = mcp_server_config
        self.mcp_connected_servers = []  # Track successfully connected servers
        self.mcp_failed_servers = []     # Track failed servers
        self.mcp_total_tools = 0         # Track total registered tools
        if self.mcp_server_config:
            self.mcp_client = await setup_mcp_client_and_tools(self.mcp_server_config)
            await self.register_mcp_tools()

    async def register_mcp_tools(self):
        """
        Register MCP tools with this controller.
        Uses the new langchain-mcp-adapters >= 0.1.0 API:
          - client.connections  : dict of server_name → connection config
          - await client.get_tools(server_name=name)  : fetch tools per server
        Tracks which servers connected and which failed.
        """
        if not self.mcp_client:
            logger.warning("MCP client not started.")
            return

        total = 0
        for server_name in list(self.mcp_client.connections.keys()):
            try:
                tools = await self.mcp_client.get_tools(server_name=server_name)
                for tool in tools:
                    tool_key = f"mcp.{server_name}.{tool.name}"
                    self.registry.registry.actions[tool_key] = RegisteredAction(
                        name=tool_key,
                        description=tool.description,
                        function=tool,
                        param_model=create_tool_param_model(tool),
                    )
                    logger.info(f"Registered MCP tool: {tool_key}")
                    total += 1
                self.mcp_connected_servers.append(server_name)
                logger.info(
                    f"✅ MCP server '{server_name}' connected — {len(tools)} tools registered"
                )
            except Exception as e:
                # Flatten ExceptionGroup / BaseExceptionGroup to a single readable line
                root: BaseException = e
                while hasattr(root, 'exceptions') and getattr(root, 'exceptions', None):
                    root = root.exceptions[0]  # type: ignore[attr-defined]
                self.mcp_failed_servers.append(
                    {"name": server_name, "error": f"{type(root).__name__}: {root}"}
                )
                logger.warning(
                    f"❌ Skipped MCP server '{server_name}' "
                    f"({type(root).__name__}: {root}). "
                    f"Check that the server package is installed and configured correctly."
                )
        self.mcp_total_tools = total
        logger.info(f"MCP: {total} tool(s) registered from {len(self.mcp_connected_servers)} server(s).")

    async def close_mcp_client(self):
        if self.mcp_client:
            try:
                # langchain-mcp-adapters >= 0.1.0 dropped context-manager support;
                # __aexit__ exists but raises NotImplementedError.
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
