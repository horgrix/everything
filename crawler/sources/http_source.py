"""
HTTP data source: aiohttp-based async requests with retry + anti-spider.

Handles task types: 'api', 'web'.
When browser config is present, delegates to BrowserSource internally.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import aiohttp

from .base import DataSource

if TYPE_CHECKING:
    from .browser_source import BrowserSource

logger = logging.getLogger(__name__)

# Default User-Agent pool (was in anti_spider.py)
_DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
]

_DEFAULT_HEADERS = {
    "User-Agent": _DEFAULT_USER_AGENTS[0],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}


class HttpSource(DataSource):
    """
    Async HTTP data source with exponential backoff retry and optional anti-spider.

    Handles:
      - task type 'api'  (JSON responses)
      - task type 'web'  (HTML responses; delegates to BrowserSource if
        browser config is present)

    Anti-spider features (from YAML anti_spider block):
      - Random delay before request
      - User-Agent rotation
      - Proxy rotation
    """

    def __init__(
        self,
        browser_source: BrowserSource | None = None,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        timeout: int = 30,
        max_redirects: int = 5,
    ):
        self._browser_source = browser_source
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._timeout = timeout
        self._max_redirects = max_redirects

    # ---- DataSource interface ----

    async def fetch(self, task_config: dict, context: dict) -> str | None:
        """
        Fetch via HTTP, or delegate to BrowserSource if browser config present.

        Returns response text, or None if URL was dedup-skipped (handled by Engine).
        """
        # Delegate to BrowserSource when browser config is present
        if task_config.get("browser") and self._browser_source:
            return await self._browser_source.fetch(task_config, context)

        url = context.get("url") or task_config.get("url", "")
        method = task_config.get("method", "GET")
        encoding = task_config.get("encoding", "utf-8")

        # Apply anti-spider delay
        await self._apply_delay(task_config)

        retry_config = task_config.get("retry", {})
        max_retries = retry_config.get("max_attempts", self._max_retries)
        backoff_base = retry_config.get("backoff_base", self._backoff_base)

        headers = self._build_headers(task_config)

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                async with aiohttp.ClientSession(
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as session:
                    async with session.request(
                        method=method,
                        url=url,
                        max_redirects=self._max_redirects,
                    ) as response:
                        if response.status < 500:
                            text = await response.text(encoding=encoding)
                            return text

                        last_error = RuntimeError(
                            f"Server returned {response.status}, URL: {url}"
                        )
                        logger.warning(
                            "Attempt %d/%d failed: %s", attempt, max_retries, last_error
                        )

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                logger.warning(
                    "Attempt %d/%d error: %s", attempt, max_retries, e
                )

            if attempt < max_retries:
                delay = backoff_base ** (attempt - 1)
                logger.debug("Waiting %.1fs before retry...", delay)
                await asyncio.sleep(delay)

        raise RuntimeError(
            f"Request failed after {max_retries} attempts, URL: {url}"
        ) from last_error

    # ---- Internal helpers ----

    async def _apply_delay(self, task_config: dict) -> None:
        """Apply random delay if anti_spider is enabled."""
        anti = task_config.get("anti_spider", {})
        if not anti.get("enabled", False):
            return
        delay_range = anti.get("delay", [1, 3])
        if not delay_range:
            return
        import random
        seconds = random.uniform(float(delay_range[0]), float(delay_range[1]))
        logger.debug("Anti-spider delay: %.2fs", seconds)
        await asyncio.sleep(seconds)

    def _build_headers(self, task_config: dict) -> dict:
        """Build request headers, optionally rotating User-Agent."""
        anti = task_config.get("anti_spider", {})
        headers = dict(_DEFAULT_HEADERS)

        if anti.get("enabled") and anti.get("rotate_user_agent"):
            import random
            agents = anti.get("user_agents", _DEFAULT_USER_AGENTS)
            headers["User-Agent"] = random.choice(agents)

        return headers

    def _get_proxy(self, task_config: dict) -> str | None:
        """Get next proxy from rotation pool (if enabled)."""
        anti = task_config.get("anti_spider", {})
        if not anti.get("enabled") or not anti.get("use_proxy"):
            return None
        proxies = anti.get("proxies", [])
        if not proxies:
            return None
        # Simple round-robin via a static counter
        if not hasattr(self, "_proxy_index"):
            self._proxy_index = 0
        proxy = proxies[self._proxy_index % len(proxies)]
        self._proxy_index += 1
        return proxy
