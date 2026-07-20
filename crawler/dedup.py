"""
URL去重模块：内存LRU + 数据库双层保障
"""

import hashlib
from cachetools import LRUCache


class URLDedup:
    """
    URL 短时去重器。

    内存层：使用 LRU 缓存记录最近5分钟内已请求的URL，
            防止同一任务周期内对相同URL的重复请求。
    数据库层：由 storage.Database 的 check_dedup / upsert_dedup 负责。

    使用方式:
        dedup = URLDedup(cache_ttl_seconds=300, max_cache_size=10000)
        if dedup.is_duplicate("https://example.com/page1"):
            print("5分钟内已请求过，跳过")
    """

    def __init__(self, cache_ttl_seconds: int = 300, max_cache_size: int = 10000):
        self._cache_ttl = cache_ttl_seconds
        # LRUCache 按时间淘汰需要配合定期检查，这里用 maxsize 限制内存
        # 实际项目中可考虑 cachetools.TTLCache 替代
        try:
            from cachetools import TTLCache
            self._cache = TTLCache(maxsize=max_cache_size, ttl=cache_ttl_seconds)
        except ImportError:
            self._cache = LRUCache(maxsize=max_cache_size)

    @staticmethod
    def hash_url(url: str) -> str:
        """对 URL 做 SHA256 哈希"""
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def is_duplicate(self, url: str) -> bool:
        """
        检查 URL 在内存缓存中是否已存在（即短时间内是否请求过）。

        返回 True 表示命中缓存（应跳过），False 表示可以发起请求。
        """
        url_hash = self.hash_url(url)
        if url_hash in self._cache:
            return True
        # 记录到缓存
        self._cache[url_hash] = True
        return False

    def clear(self):
        """清空内存缓存"""
        self._cache.clear()

    def __len__(self):
        return len(self._cache)