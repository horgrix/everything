"""
反反爬策略模块：可开关的反爬应对措施。

支持策略：
  - 随机请求延迟（delay）
  - User-Agent 轮换（user_agent_rotation）
  - 代理 IP 支持（proxies）
"""

import asyncio
import random
import logging

logger = logging.getLogger(__name__)

# 预置 User-Agent 池
_DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
]


class AntiSpider:
    """
    反反爬策略管理器。

    根据 YAML 配置中的 anti_spider 块决定是否启用各项策略。

    使用方式:
        anti = AntiSpider(config={"enabled": True, "delay": [1, 3]})
        async with anti:
            response = await fetcher.fetch(url)
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", False)
        self._delay_range = self.config.get("delay", [1, 3])
        self._use_proxy = self.config.get("use_proxy", False)
        self._proxies = self.config.get("proxies", [])
        self._rotate_ua = self.config.get("rotate_user_agent", False)
        self._user_agents = self.config.get("user_agents", _DEFAULT_USER_AGENTS)
        self._proxy_index = 0

    async def delay(self):
        """在请求前执行随机延迟"""
        if not self.enabled:
            return
        if not self._delay_range:
            return
        seconds = random.uniform(self._delay_range[0], self._delay_range[1])
        logger.debug("反爬延迟 %.2f 秒", seconds)
        await asyncio.sleep(seconds)

    def get_proxy(self) -> str | None:
        """获取下一个代理地址（轮询）"""
        if not self.enabled or not self._use_proxy or not self._proxies:
            return None
        proxy = self._proxies[self._proxy_index % len(self._proxies)]
        self._proxy_index += 1
        logger.debug("使用代理: %s", proxy)
        return proxy

    def get_user_agent(self) -> str:
        """随机获取一个 User-Agent"""
        if not self.enabled or not self._rotate_ua:
            return self._user_agents[0]
        return random.choice(self._user_agents)

    async def __aenter__(self):
        await self.delay()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass  # 无需清理