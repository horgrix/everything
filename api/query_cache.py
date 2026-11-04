"""只读 SQL 查询结果的内存 TTL 缓存。

前端首屏会反复发送同一批只读 SQL（其中「游戏名称/图标/标签映射」一条就 250KB+），
而数据仓库是每 2 小时才更新一次，因此对查询结果做短 TTL 缓存：

- 命中时直接返回已经序列化好的 JSON bytes，省掉 SQL 执行与 JSON 序列化；
- 任何写接口（batch insert / update / delete / truncate）都会清空缓存；
- TTL 到点自动失效：即使写入来自进程外（爬虫任务直连 SQLite），最长也只陈旧 TTL 秒。

配置（环境变量）：
    API_QUERY_CACHE_TTL   缓存有效期，秒，默认 60；<=0 关闭缓存
    API_QUERY_CACHE_MAX   最大条目数，默认 64
    API_QUERY_CACHE_BYTES 最大缓存字节数，默认 32MB
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

DEFAULT_TTL_SECONDS = 60
DEFAULT_MAX_ENTRIES = 64
DEFAULT_MAX_BYTES = 32 * 1024 * 1024


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@dataclass
class _Entry:
    expires_at: float
    payload: bytes


class QueryCache:
    """按 key（这里是 SQL 原文）缓存序列化后的响应体。"""

    def __init__(
        self,
        ttl: float = DEFAULT_TTL_SECONDS,
        maxsize: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        clock=time.monotonic,
    ):
        self.ttl = float(ttl)
        self.maxsize = max(1, int(maxsize))
        self.max_bytes = max(0, int(max_bytes))
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, _Entry]" = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def get(self, key: str):
        """命中返回 bytes，未命中/已过期/已关闭返回 None。"""
        if not self.enabled:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            if entry.expires_at <= self._clock():
                self._drop(key, entry)
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return entry.payload

    def set(self, key: str, payload: bytes) -> None:
        if not self.enabled:
            return
        if self.max_bytes and len(payload) > self.max_bytes:
            return
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= len(old.payload)
            self._entries[key] = _Entry(self._clock() + self.ttl, payload)
            self._bytes += len(payload)
            while self._entries and (
                len(self._entries) > self.maxsize
                or (self.max_bytes and self._bytes > self.max_bytes)
            ):
                evicted_key, evicted = self._entries.popitem(last=False)
                self._bytes -= len(evicted.payload)
                self.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "enabled": self.enabled,
                "ttl": self.ttl,
                "maxsize": self.maxsize,
                "max_bytes": self.max_bytes,
                "size": len(self._entries),
                "bytes": self._bytes,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
            }

    def _drop(self, key: str, entry: _Entry) -> None:
        del self._entries[key]
        self._bytes -= len(entry.payload)

    @classmethod
    def from_env(cls) -> "QueryCache":
        return cls(
            ttl=_env_int("API_QUERY_CACHE_TTL", DEFAULT_TTL_SECONDS),
            maxsize=_env_int("API_QUERY_CACHE_MAX", DEFAULT_MAX_ENTRIES),
            max_bytes=_env_int("API_QUERY_CACHE_BYTES", DEFAULT_MAX_BYTES),
        )
