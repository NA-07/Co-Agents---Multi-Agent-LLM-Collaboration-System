import logging
import os
import random
import platform
from typing import Optional

from browser_use.browser.browser import Browser, IN_DOCKER
from browser_use.browser.context import BrowserContext, BrowserContextConfig, BrowserContextState
from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import BrowserContext as PlaywrightBrowserContext

logger = logging.getLogger(__name__)


def _generate_realistic_ua() -> str:
    """Generate a realistic Chrome user-agent matching the current OS platform.

    Rotates Chrome versions slightly to avoid fingerprinting via a single
    hardcoded version string.
    """
    _chrome_versions = ["131.0.0.0", "132.0.0.0", "133.0.0.0", "134.0.0.0", "135.0.0.0"]
    chrome_ver = random.choice(_chrome_versions)

    sys_platform = platform.system()
    if sys_platform == "Windows":
        os_token = "Windows NT 10.0; Win64; x64"
    elif sys_platform == "Darwin":
        os_token = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        os_token = "X11; Linux x86_64"

    return (
        f"Mozilla/5.0 ({os_token}) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{chrome_ver} Safari/537.36"
    )


# Generate once per process start so the same UA is used across all contexts
_DEFAULT_UA = _generate_realistic_ua()


class CustomBrowserContext(BrowserContext):
    def __init__(
            self,
            browser: "Browser",
            config: BrowserContextConfig | None = None,
            state: Optional[BrowserContextState] = None,
    ):
        super().__init__(browser=browser, config=config, state=state)

    async def _create_context(self, browser: PlaywrightBrowser) -> PlaywrightBrowserContext:
        """
        Override base context creation to:
        1. Apply playwright-stealth (patches ~18 bot-detection signals) — unless
           the user disabled Stealth Mode in Browser Settings.
        2. Inject extra JS init-scripts for belt-and-suspenders protection.
        3. Set a realistic user-agent so sites cannot fingerprint via UA.
        """
        context: PlaywrightBrowserContext = await super()._create_context(browser)

        # Check whether the user turned stealth OFF via the UI checkbox.
        # The agent tab passes "--no-stealth" as a sentinel in extra_browser_args.
        _extra_args = list(getattr(self.browser.config, "extra_browser_args", []) or [])
        stealth_disabled = "--no-stealth" in _extra_args

        if not stealth_disabled:
            # ------------------------------------------------------------------ #
            #  playwright-stealth patches (applied to the whole context once)      #
            # ------------------------------------------------------------------ #
            try:
                from playwright_stealth import Stealth
                stealth = Stealth(
                    navigator_webdriver=True,           # #1 detection signal
                    navigator_user_agent=True,
                    navigator_user_agent_override=_DEFAULT_UA,
                    navigator_platform=True,
                    navigator_platform_override="Win32",
                    navigator_vendor=True,
                    navigator_vendor_override="Google Inc.",
                    navigator_languages=True,
                    navigator_languages_override=("en-US", "en"),
                    chrome_app=True,
                    chrome_csi=True,
                    chrome_load_times=True,
                    chrome_runtime=False,               # True can break some pages
                    navigator_hardware_concurrency=True,
                    navigator_plugins=True,
                    media_codecs=True,
                    webgl_vendor=True,
                    sec_ch_ua=True,
                )
                await stealth.apply_stealth_async(context)
                logger.info("Stealth mode applied to browser context")
            except ImportError:
                logger.warning(
                    "playwright-stealth not installed — running without stealth. "
                    "Install with: pip install playwright-stealth"
                )
            except Exception as e:
                logger.warning("Failed to apply stealth patches: %s", e)
        else:
            logger.info("Stealth mode DISABLED by user preference")

        # ------------------------------------------------------------------ #
        #  Extra JS init-script injected on every new page load               #
        # ------------------------------------------------------------------ #
        await context.add_init_script("""
            // Remove the 'webdriver' property exposed by CDP/Playwright
            try {
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                    configurable: true,
                });
            } catch (e) {}

            // Restore chrome.runtime if missing (some detectors check for it)
            try {
                if (!window.chrome) { window.chrome = {}; }
                if (!window.chrome.runtime) { window.chrome.runtime = {}; }
            } catch (e) {}

            // Spoof permissions.query so 'notifications' returns real state
            try {
                const _origQuery = window.navigator.permissions.query.bind(
                    window.navigator.permissions
                );
                window.navigator.permissions.query = (params) =>
                    params.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : _origQuery(params);
            } catch (e) {}

            // Prevent iframe srcdoc webdriver leak
            try {
                const _origDescriptor = Object.getOwnPropertyDescriptor(
                    HTMLIFrameElement.prototype, 'contentWindow'
                );
                Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
                    get: function () {
                        const win = _origDescriptor.get.call(this);
                        try {
                            Object.defineProperty(win.navigator, 'webdriver', {
                                get: () => undefined,
                                configurable: true,
                            });
                        } catch (e) {}
                        return win;
                    },
                });
            } catch (e) {}
        """)

        return context

