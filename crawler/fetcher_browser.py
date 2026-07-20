"""
浏览器 HTTP 请求模块：基于 Playwright 的无头浏览器引擎。

用于处理 JavaScript 动态渲染的页面，支持：
  - 等待指定选择器出现（wait）
  - 点击按钮/加载更多（click）
  - 页面滚动加载（scroll）
  - 截图调试（screenshot）
  - 操作完成后返回完整 HTML

使用方式:
    fetcher = BrowserFetcher(headless=True)
    html = await fetcher.fetch(
        url="https://example.com",
        browser_config={
            "wait_selector": "table.data-table",
            "wait_timeout": 10000,
            "actions": [
                {"type": "click", "selector": "button.load-more", "wait_after": 2000},
                {"type": "scroll", "repeat": 3, "wait_after": 1000},
            ]
        }
    )
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class BrowserFetcher:
    """
    Playwright 无头浏览器请求器。

    特性：
    - 使用 Playwright Chromium 引擎渲染 JavaScript 动态页面
    - 支持等待选择器出现（动态加载表格等）
    - 支持点击按钮（加载更多、同意 Cookie 等）
    - 支持页面滚动（触发无限滚动加载）
    - 操作完成后返回 page.content() 完整 HTML

    依赖安装：
        pip install playwright
        playwright install chromium
    """

    def __init__(self, headless: bool = True, timeout: int = 30000):
        self._headless = headless
        self._timeout = timeout
        self._browser = None
        self._playwright = None

    async def _ensure_browser(self):
        """懒加载浏览器实例"""
        if self._browser is not None:
            return

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "使用浏览器模式需要安装 Playwright:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
        )
        logger.info("Playwright 浏览器已启动 (headless=%s)", self._headless)

    async def fetch(self, url: str, browser_config: dict = None) -> str:
        """
        使用浏览器请求 URL 并返回渲染后的 HTML。

        参数:
            url: 目标 URL
            browser_config: YAML 中的 browser 配置块
                {
                    "headless": True,
                    "wait_selector": "table.data-table",
                    "wait_timeout": 10000,
                    "actions": [
                        {"type": "click", "selector": "button.load-more", "wait_after": 2000},
                        {"type": "scroll", "repeat": 3, "wait_after": 1000},
                    ],
                    "screenshot": "debug.png",  # 可选：截图保存路径
                }

        返回:
            渲染后的完整 HTML 字符串

        抛出:
            Exception: 浏览器操作失败
        """
        if browser_config is None:
            browser_config = {}

        headless = browser_config.get("headless", self._headless)
        self._headless = headless

        await self._ensure_browser()

        context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        try:
            logger.info("浏览器请求: %s", url)
            await page.goto(url, wait_until="domcontentloaded", timeout=self._timeout)

            # 1. 等待指定选择器出现
            wait_selector = browser_config.get("wait_selector")
            wait_timeout = browser_config.get("wait_timeout", 15000)

            if wait_selector:
                logger.info("等待选择器出现: %s (timeout=%dms)", wait_selector, wait_timeout)
                try:
                    await page.wait_for_selector(wait_selector, timeout=wait_timeout)
                    logger.info("选择器 '%s' 已就绪", wait_selector)
                except Exception as e:
                    logger.warning("等待选择器 '%s' 超时: %s", wait_selector, e)

            # 2. 执行操作序列
            actions = browser_config.get("actions", [])
            for i, action in enumerate(actions):
                action_type = action.get("type", "")
                wait_after = action.get("wait_after", 1000)

                try:
                    if action_type == "click":
                        selector = action.get("selector", "")
                        if selector:
                            logger.info("[操作 %d/%d] 点击: %s", i + 1, len(actions), selector)
                            await page.click(selector, timeout=5000)

                    elif action_type == "scroll":
                        repeat = action.get("repeat", 1)
                        for j in range(repeat):
                            logger.info("[操作 %d/%d] 滚动: %d/%d", i + 1, len(actions), j + 1, repeat)
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            await asyncio.sleep(wait_after / 1000)

                    elif action_type == "wait":
                        ms = action.get("ms", 1000)
                        await asyncio.sleep(ms / 1000)

                except Exception as e:
                    logger.warning("操作 '%s' 失败: %s", action_type, e)

                # 操作后等待
                if action_type not in ("wait", "scroll"):
                    await asyncio.sleep(wait_after / 1000)

            # 3. 截图 / HTML快照（调试用）
            screenshot_path = browser_config.get("screenshot")
            if screenshot_path:
                if screenshot_path.endswith(".html"):
                    html = await page.content()
                    with open(screenshot_path, "w", encoding="utf-8") as f:
                        f.write(html)
                    logger.info("HTML快照已保存: %s (%d 字符)", screenshot_path, len(html))
                else:
                    await page.screenshot(path=screenshot_path, full_page=True)
                    logger.info("截图已保存: %s", screenshot_path)

            # 4. 返回完整 HTML
            html = await page.content()
            logger.info("浏览器页面获取完成 (%d 字符)", len(html))
            return html

        finally:
            await context.close()

    async def close(self):
        """关闭浏览器"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
            logger.info("Playwright 浏览器已关闭")