"""Browser session management for the automation layer.

Owns the Playwright lifecycle (launch, context, page) and screenshot
capture. Contains no recruitment-domain knowledge — all domain actions live
in actions.py.
"""

import itertools
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

DEFAULT_SCREENSHOT_DIR = Path("screenshots")


class BrowserSession:
    """A managed Playwright Chromium session.

    Usage:
        session = BrowserSession(base_url="http://127.0.0.1:5000")
        session.launch()
        page = session.page
        ...
        session.close()
    """

    def __init__(
        self,
        base_url: str,
        headless: bool = True,
        screenshot_dir: str | Path = DEFAULT_SCREENSHOT_DIR,
        navigation_timeout_ms: int = 10_000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headless = headless
        self.screenshot_dir = Path(screenshot_dir)
        self.navigation_timeout_ms = navigation_timeout_ms

        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._screenshot_counter = itertools.count(1)

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser session is not launched; call launch() first")
        return self._page

    @property
    def is_launched(self) -> bool:
        return self._page is not None

    def launch(self) -> None:
        """Launch Chromium and open a fresh page."""
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
        )
        self._page = self._context.new_page()
        self._page.set_default_navigation_timeout(self.navigation_timeout_ms)
        self._page.set_default_timeout(self.navigation_timeout_ms)

    def close(self) -> None:
        """Tear down the browser, context, and Playwright runtime."""
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    def url_for(self, path: str) -> str:
        """Build an absolute URL from an app path."""
        return f"{self.base_url}/{path.lstrip('/')}"

    def goto(self, path: str) -> None:
        """Navigate to an app path and wait for the page to settle."""
        self.page.goto(self.url_for(path), wait_until="domcontentloaded")

    def screenshot(self, label: str, *, success: bool = True) -> str | None:
        """Capture a screenshot labelled with the action and outcome.

        Returns the relative path of the saved screenshot, or None if the
        session is not launched.
        """
        if self._page is None:
            return None
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        outcome = "ok" if success else "fail"
        path = self.screenshot_dir / f"{timestamp}-{next(self._screenshot_counter):02d}-{label}-{outcome}.png"
        self._page.screenshot(path=str(path), full_page=True)
        return str(path)

    def __enter__(self) -> "BrowserSession":
        self.launch()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
