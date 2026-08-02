"""
Browser data source: Playwright-based headless browser for JS-rendered pages.

Handles task types: 'web' when browser config is present.
"""

import asyncio
import logging
from typing import Any

from .base import DataSource

logger = logging.getLogger(__name__)


class BrowserSource(DataSource):
    """
    Playwright headless browser data source.

    Supports:
      - Waiting for selectors to appear
      - Action sequences (click, scroll, wait)
      - Screenshot / HTML snapshot for debugging
      - Returns page.content() as HTML string

    Dependencies:
        pip install playwright
        playwright install chromium
    """

    def __init__(self, headless: bool = True, timeout: int = 30000):
        self._headless = headless
        self._timeout = timeout
        self._browser = None
        self._playwright = None

    # ---- DataSource interface ----

    async def fetch(self, task_config: dict, context: dict) -> str:
        """Fetch page content via headless browser."""
        url = context.get("url") or task_config.get("url", "")
        browser_config = task_config.get("browser", {})

        headless = browser_config.get("headless", self._headless)
        self._headless = headless

        await self._ensure_browser()

        context_obj = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        page = await context_obj.new_page()

        try:
            logger.info("Browser request: %s", url)
            await page.goto(url, wait_until="domcontentloaded", timeout=self._timeout)

            # 1. Wait for selector
            await self._wait_for_selector(page, browser_config)

            # 2. Execute action sequence
            await self._execute_actions(page, browser_config)

            # 3. Optional screenshot / HTML snapshot
            await self._take_screenshot(page, browser_config)

            # 4. Return full HTML
            html = await page.content()
            logger.info("Browser page fetched (%d chars)", len(html))
            return html

        finally:
            await context_obj.close()

    async def close(self) -> None:
        """Close the browser instance."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
            logger.info("Playwright browser closed")

    # ---- Internal helpers ----

    async def _ensure_browser(self) -> None:
        """Lazily launch the browser."""
        if self._browser is not None:
            return

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "Browser mode requires Playwright:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
        )
        logger.info("Playwright browser started (headless=%s)", self._headless)

    async def _wait_for_selector(self, page, browser_config: dict) -> None:
        """Wait for a CSS selector to appear."""
        selector = browser_config.get("wait_selector")
        if not selector:
            return
        timeout = browser_config.get("wait_timeout", 15000)
        logger.info("Waiting for selector: %s (timeout=%dms)", selector, timeout)
        try:
            await page.wait_for_selector(selector, timeout=timeout)
            logger.info("Selector '%s' ready", selector)
        except Exception as e:
            logger.warning("Wait for selector '%s' timed out: %s", selector, e)

    async def _execute_actions(self, page, browser_config: dict) -> None:
        """Execute a sequence of page interactions."""
        actions = browser_config.get("actions", [])
        for i, action in enumerate(actions):
            action_type = action.get("type", "")
            wait_after = action.get("wait_after", 1000)

            try:
                if action_type == "click":
                    selector = action.get("selector", "")
                    if selector:
                        logger.info("[Action %d/%d] click: %s", i + 1, len(actions), selector)
                        await page.click(selector, timeout=5000)

                elif action_type == "scroll":
                    repeat = action.get("repeat", 1)
                    for j in range(repeat):
                        logger.info("[Action %d/%d] scroll: %d/%d", i + 1, len(actions), j + 1, repeat)
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(wait_after / 1000)

                elif action_type == "wait":
                    ms = action.get("ms", 1000)
                    await asyncio.sleep(ms / 1000)

            except Exception as e:
                logger.warning("Action '%s' failed: %s", action_type, e)

            # Post-action wait (except for wait/scroll which handle their own)
            if action_type not in ("wait", "scroll"):
                await asyncio.sleep(wait_after / 1000)

    @staticmethod
    async def _take_screenshot(page, browser_config: dict) -> None:
        """Save screenshot or HTML snapshot for debugging."""
        path = browser_config.get("screenshot")
        if not path:
            return
        if path.endswith(".html"):
            html = await page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info("HTML snapshot saved: %s (%d chars)", path, len(html))
        else:
            await page.screenshot(path=path, full_page=True)
            logger.info("Screenshot saved: %s", path)
