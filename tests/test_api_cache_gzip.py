"""GZip 压缩与只读 SQL 查询缓存（/api/data/sql）测试。"""

import pytest

from api import create_app
from api.query_cache import QueryCache


# ================================================================
# QueryCache 单元测试（用假时钟，不依赖真实等待）
# ================================================================


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestQueryCache:
    def test_hit_and_miss(self):
        cache = QueryCache(ttl=60)
        assert cache.get("k") is None
        cache.set("k", b"payload")
        assert cache.get("k") == b"payload"
        stats = cache.stats()
        assert stats["hits"] == 1 and stats["misses"] == 1

    def test_expires_after_ttl(self):
        clock = FakeClock()
        cache = QueryCache(ttl=10, clock=clock)
        cache.set("k", b"payload")
        clock.now += 9.9
        assert cache.get("k") == b"payload"
        clock.now += 0.2
        assert cache.get("k") is None
        assert cache.stats()["size"] == 0

    def test_disabled_when_ttl_zero(self):
        cache = QueryCache(ttl=0)
        cache.set("k", b"payload")
        assert cache.get("k") is None
        assert cache.stats()["enabled"] is False

    def test_evicts_lru_beyond_maxsize(self):
        cache = QueryCache(ttl=60, maxsize=2)
        cache.set("a", b"1")
        cache.set("b", b"2")
        cache.get("a")            # a 变成最近使用
        cache.set("c", b"3")      # 淘汰 b
        assert cache.get("a") == b"1"
        assert cache.get("b") is None
        assert cache.get("c") == b"3"
        assert cache.stats()["evictions"] == 1

    def test_evicts_by_bytes(self):
        cache = QueryCache(ttl=60, maxsize=10, max_bytes=10)
        cache.set("a", b"x" * 6)
        cache.set("b", b"y" * 6)  # 超过 max_bytes，淘汰 a
        assert cache.get("a") is None
        assert cache.get("b") == b"y" * 6
        assert cache.stats()["bytes"] == 6

    def test_skips_payload_larger_than_budget(self):
        cache = QueryCache(ttl=60, max_bytes=8)
        cache.set("big", b"x" * 9)
        assert cache.get("big") is None

    def test_clear(self):
        cache = QueryCache(ttl=60)
        cache.set("a", b"1")
        cache.clear()
        assert cache.stats()["size"] == 0
        assert cache.get("a") is None

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("API_QUERY_CACHE_TTL", "120")
        monkeypatch.setenv("API_QUERY_CACHE_MAX", "8")
        cache = QueryCache.from_env()
        assert cache.ttl == 120 and cache.maxsize == 8

    def test_from_env_bad_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("API_QUERY_CACHE_TTL", "not-a-number")
        assert QueryCache.from_env().ttl == 60


# ================================================================
# 接口级测试：真实 FastAPI 应用 + ASGI 直连
# ================================================================
#
# 用 httpx.ASGITransport 而不是 TestClient：Database 是单连接，必须在同一个线程里
# 创建和使用（TestClient 会把应用跑在子线程，触发 sqlite3 的线程检查）。

import asyncio

import httpx

SQL = "SELECT id, name, payload FROM demo ORDER BY id"


def call(app, method, path, **kwargs):
    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(_run())


@pytest.fixture
def app(tmp_path):
    application = create_app(
        config_dir=str(tmp_path / "tasks"),
        db_path=str(tmp_path / "crawler.db"),
    )
    conn = application.state.db.conn
    conn.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT, payload TEXT)")
    rows = [(i, f"game-{i}", "x" * 200) for i in range(1, 41)]
    conn.executemany("INSERT INTO demo (id, name, payload) VALUES (?, ?, ?)", rows)
    conn.commit()
    yield application


class TestSqlEndpoint:
    def test_response_shape(self, app):
        resp = call(app, "POST", "/api/data/sql", json={"sql": SQL})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0 and body["total"] == 40
        assert body["data"][0]["name"] == "game-1"

    def test_gzip_compresses_large_json(self, app):
        resp = call(
            app,
            "POST",
            "/api/data/sql",
            json={"sql": SQL},
            headers={"Accept-Encoding": "gzip"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-encoding") == "gzip"
        # content-length 是压缩后的字节数，resp.content 是解压后的内容
        assert int(resp.headers["content-length"]) < len(resp.content)
        assert resp.json()["total"] == 40

    def test_small_json_not_compressed(self, app):
        resp = call(
            app,
            "POST",
            "/api/data/sql",
            json={"sql": "SELECT 1 AS n"},
            headers={"Accept-Encoding": "gzip"},
        )
        assert resp.json()["data"] == [{"n": 1}]
        assert "content-encoding" not in resp.headers

    def test_cors_headers_survive_gzip(self, app):
        """跨域调用方（data.horgrix.com → horgrix.com/api/data）压缩后仍要有 CORS 头。"""
        resp = call(
            app,
            "POST",
            "/api/data/sql",
            json={"sql": SQL},
            headers={
                "Accept-Encoding": "gzip",
                "Origin": "https://data.horgrix.com",
            },
        )
        assert resp.headers.get("content-encoding") == "gzip"
        # allow_credentials=True 时 Starlette 会回显具体 Origin，而不是 *
        assert resp.headers.get("access-control-allow-origin") in (
            "*",
            "https://data.horgrix.com",
        )
        assert resp.json()["total"] == 40

    def test_second_identical_query_hits_cache(self, app):
        first = call(app, "POST", "/api/data/sql", json={"sql": SQL})
        second = call(app, "POST", "/api/data/sql", json={"sql": SQL})
        assert first.headers["x-cache"] == "MISS"
        assert second.headers["x-cache"] == "HIT"
        assert second.json() == first.json()

    def test_different_sql_is_separate_entry(self, app):
        call(app, "POST", "/api/data/sql", json={"sql": SQL})
        resp = call(
            app, "POST", "/api/data/sql", json={"sql": "SELECT COUNT(*) AS cnt FROM demo"}
        )
        assert resp.headers["x-cache"] == "MISS"

    def test_failed_sql_not_cached(self, app):
        bad = {"sql": "SELECT * FROM missing_table"}
        assert call(app, "POST", "/api/data/sql", json=bad).status_code == 400
        assert call(app, "POST", "/api/data/sql", json=bad).status_code == 400

    def test_write_invalidates_cache(self, app):
        call(app, "POST", "/api/data/sql", json={"sql": SQL})
        assert call(app, "POST", "/api/data/sql", json={"sql": SQL}).headers["x-cache"] == "HIT"

        resp = call(
            app,
            "POST",
            "/api/data/demo/rows/batch",
            json={"rows": [{"id": 99, "name": "new-game", "payload": "y" * 200}]},
        )
        assert resp.status_code == 200

        after = call(app, "POST", "/api/data/sql", json={"sql": SQL})
        assert after.headers["x-cache"] == "MISS"
        assert after.json()["total"] == 41

    def test_delete_invalidates_cache(self, app):
        call(app, "POST", "/api/data/sql", json={"sql": SQL})
        assert call(app, "POST", "/api/data/sql", json={"sql": SQL}).headers["x-cache"] == "HIT"

        assert call(app, "DELETE", "/api/data/demo/rows/id/1").status_code == 200
        after = call(app, "POST", "/api/data/sql", json={"sql": SQL})
        assert after.headers["x-cache"] == "MISS"
        assert after.json()["total"] == 39

    def test_cache_stats_endpoint(self, app):
        call(app, "POST", "/api/data/sql", json={"sql": SQL})
        call(app, "POST", "/api/data/sql", json={"sql": SQL})
        stats = call(app, "GET", "/api/system/query-cache").json()["data"]
        assert stats["enabled"] is True and stats["ttl"] == 60
        assert stats["hits"] == 1 and stats["misses"] == 1
        assert stats["hit_rate"] == 0.5
