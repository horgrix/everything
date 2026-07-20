"""
HTTP 请求模块：基于 aiohttp 的异步请求，支持指数退避重试。
"""

import asyncio
import logging
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)


class Fetcher:
    """
    异步 HTTP 请求器。

    特性：
    - 基于 aiohttp 异步请求
    - 指数退避重试（默认3次，1s/2s/4s）
    - 仅对网络错误和 5xx 状态码重试，4xx 不重试
    - 超时控制

    使用方式:
        fetcher = Fetcher(max_retries=3, backoff_base=2)
        html = await fetcher.fetch("https://example.com")
    """

    def __init__(self, max_retries: int = 3, backoff_base: float = 2.0,
                 timeout: int = 30, max_redirects: int = 5):
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeout = timeout
        self.max_redirects = max_redirects

        # Cookie / header 可以在 session 级别设置
        self._default_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        }

    async def fetch(self, url: str, method: str = "GET",
                    headers: dict = None, data: dict = None,
                    json_data: dict = None) -> str:
        """
        发起 HTTP 请求并返回响应文本。

        参数:
            url: 请求 URL
            method: HTTP 方法（GET / POST）
            headers: 额外的请求头（会合并到默认头中）
            data: 表单数据（application/x-www-form-urlencoded）
            json_data: JSON 数据

        返回:
            响应文本内容

        抛出:
            RuntimeError: 重试次数耗尽后仍失败
        """
        # 合并 headers
        req_headers = {**self._default_headers}
        if headers:
            req_headers.update(headers)

        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                async with aiohttp.ClientSession(
                    headers=req_headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as session:
                    async with session.request(
                        method=method,
                        url=url,
                        data=data,
                        json=json_data,
                        max_redirects=self.max_redirects,
                    ) as response:
                        # 检查状态码
                        if response.status < 500:
                            # 2xx/3xx/4xx：直接返回
                            # 注意：有些网站 4xx 也可能需要重试（如 429 Too Many Requests）
                            # 这里遵循约定：4xx 不重试
                            text = await response.text()
                            return text

                        # 5xx：服务器错误，触发重试
                        last_error = RuntimeError(
                            f"服务器返回 {response.status}，URL: {url}"
                        )
                        logger.warning(
                            "第 %d/%d 次请求失败: %s",
                            attempt, self.max_retries, last_error
                        )

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                logger.warning(
                    "第 %d/%d 次请求异常: %s",
                    attempt, self.max_retries, e
                )

            # 如果不是最后一次尝试，等待后退避
            if attempt < self.max_retries:
                delay = self.backoff_base ** (attempt - 1)
                logger.debug("等待 %.1f 秒后重试...", delay)
                await asyncio.sleep(delay)

        # 所有重试耗尽
        raise RuntimeError(
            f"请求失败，已重试 {self.max_retries} 次，URL: {url}"
        ) from last_error

    async def fetch_json(self, url: str, method: str = "GET",
                         headers: dict = None, data: dict = None,
                         json_data: dict = None) -> dict:
        """
        简化的 JSON API 请求，直接返回解析后的 dict。
        """
        text = await self.fetch(url, method=method, headers=headers,
                                data=data, json_data=json_data)
        import json
        return json.loads(text)