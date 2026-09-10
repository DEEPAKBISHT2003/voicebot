import asyncio
from typing import Optional
from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext, Page
from loguru import logger


class TeamsBrowser:
    """Manages the Playwright browser lifecycle, Chromium launch flags, context, and CDP settings for MS Teams."""

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        headless: bool = True,
        browser_ws_url: str = "ws://127.0.0.1:8000",
        backend_ws_base: str = "ws://127.0.0.1:8000",
        user_agent: Optional[str] = None,
    ):
        self.headless = headless
        self.browser_ws_url = browser_ws_url
        self.backend_ws_base = backend_ws_base
        self.user_agent = user_agent or self.DEFAULT_USER_AGENT

        self._playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self) -> None:
        """Launch Chromium with WebRTC and media stream bypass arguments."""
        self._playwright = await async_playwright().start()

        launch_args = [
            "--use-fake-ui-for-media-stream",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-popup-blocking",
            "--disable-external-intent-requests",
            "--allow-insecure-localhost",
            "--disable-web-security",
            "--allow-running-insecure-content",
            "--ignore-certificate-errors",
            f"--unsafely-treat-insecure-origin-as-secure={self.browser_ws_url}",
            f"--unsafely-treat-insecure-origin-as-secure={self.backend_ws_base}",
            "--disable-features=BlockInsecurePrivateNetworkRequests,BlockInsecurePrivateNetworkRequestsFromPrivateNetwork,ExternalProtocolHandler,PrivateNetworkAccessPermissionPrompt,LocalNetworkAccessChecks",
        ]

        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=launch_args,
        )

        self.context = await self.browser.new_context(
            permissions=["microphone", "camera"],
            bypass_csp=True,
            user_agent=self.user_agent,
        )

        # Suppress msteams:// and intent:// modal prompts
        await self.context.route(
            "**/*",
            lambda route: route.abort() if route.request.url.startswith(("msteams:", "intent:")) else route.continue_(),
        )

        # Override navigator.webdriver to undefined to prevent Teams "Suspected threat / Unverified" blocking
        await self.context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

    async def create_page(self, init_script: Optional[str] = None) -> Page:
        """Create a new page, configure CSP bypass via CDP, and attach console logging."""
        if not self.context:
            raise RuntimeError("BrowserContext is not initialized. Call start() first.")

        if init_script:
            await self.context.add_init_script(init_script)

        self.page = await self.context.new_page()
        self.page.on("console", lambda msg: logger.info(f"[BrowserConsole] {msg.type}: {msg.text}"))

        try:
            cdp = await self.context.new_cdp_session(self.page)
            await cdp.send("Page.setBypassCSP", {"enabled": True})
            logger.info("[TeamsBot] CDP Page.setBypassCSP enabled successfully.")
        except Exception as cdpe:
            logger.warning(f"[TeamsBot] Could not set CDP Page.setBypassCSP: {cdpe}")

        return self.page

    async def close(self) -> None:
        """Clean up page, context, browser, and Playwright instances."""
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None

        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
