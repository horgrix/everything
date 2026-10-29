"""Tests for 会话式翻页（pagination：第一页拿 session，后续页带 offset 继续翻）。"""

import asyncio
import json
from unittest import mock

import pytest

from crawler.engine import CrawlerEngine
from crawler.pagination import (
    build_page_url,
    extract_session,
    extract_value,
    page_offset,
    resolve_pagination,
)
from crawler.sources.base import DataSource, SourceRegistry
from storage.database import Database


class TestResolvePagination:
    def test_no_config(self):
        assert resolve_pagination({}) is None
        assert resolve_pagination({"pagination": {}}) is None

    def test_single_page_is_noop(self):
        """max_pages < 2 视为不需要翻页。"""
        assert resolve_pagination({"pagination": {"limit": 10, "max_pages": 1}}) is None

    def test_defaults(self):
        cfg = resolve_pagination({"pagination": {"limit": 10, "max_pages": 3}})
        assert cfg["offset_param"] == "from"
        assert cfg["limit_param"] == "limit"
        assert cfg["session_param"] == "session_id"
        assert cfg["max_pages"] == 3

    def test_page_size_alias(self):
        cfg = resolve_pagination({"pagination": {"page_size": 20, "pages": 2}})
        assert cfg["limit"] == 20 and cfg["max_pages"] == 2


class TestPageOffset:
    def test_offsets(self):
        assert [page_offset(p, 10) for p in (1, 2, 3)] == [0, 10, 20]


class TestExtractValue:
    def test_dict_path(self):
        assert extract_value({"data": {"a": [{"b": 1}]}}, "data.a.0.b") == 1

    def test_json_string(self):
        assert extract_value('{"data": {"next": "x"}}', "data.next") == "x"

    def test_missing(self):
        assert extract_value({"data": {}}, "data.next_page") is None
        assert extract_value("not json", "data.next") is None


class TestExtractSession:
    URL = ("https://api.taptapdada.com/landing/v7/timeline"
           "?from=10&limit=10&session_id=938b540c-964a-45b6-8195-86554c72d2e6")

    def test_from_next_page_url(self):
        payload = {"data": {"next_page": self.URL}}
        assert extract_session(payload, "data.next_page", "session_id") == \
            "938b540c-964a-45b6-8195-86554c72d2e6"

    def test_from_plain_value(self):
        payload = {"data": {"session_id": "abc-123"}}
        assert extract_session(payload, "data.session_id", "session_id") == "abc-123"

    def test_url_without_param(self):
        payload = {"data": {"next_page": "https://x/y?from=10"}}
        assert extract_session(payload, "data.next_page", "session_id") is None

    def test_empty(self):
        assert extract_session({"data": {}}, "data.next_page", "session_id") is None


class TestBuildPageUrl:
    def test_appends_params(self):
        url = build_page_url("https://x/y?a=1&limit=10&rec_tag=",
                             {"from": 10, "session_id": "sid"})
        assert "a=1" in url and "limit=10" in url and "rec_tag=" in url
        assert "from=10" in url and "session_id=sid" in url

    def test_overrides_existing(self):
        url = build_page_url("https://x/y?from=0&limit=10", {"from": 20})
        assert url.count("from=") == 1 and "from=20" in url


class _PagingSource(DataSource):
    """按 URL 里的 from 返回不同页；记录请求过的 URL。"""

    def __init__(self):
        self.calls = []

    async def fetch(self, task_config, context):
        url = context.get("url")
        self.calls.append(url)
        if "from=" not in url:
            return json.dumps({
                "data": {
                    "list": [{"app_id": i} for i in range(1, 11)],
                    "next_page": "https://api.x/y?from=10&limit=10&session_id=SID-1",
                }
            })
        from_val = int(url.split("from=")[1].split("&")[0])
        last = from_val >= 20
        return json.dumps({
            "data": {
                "list": [{"app_id": i} for i in range(from_val + 1, from_val + 11)],
                **({} if last else {"next_page":
                                    f"https://api.x/y?from={from_val + 10}&limit=10&session_id=SID-1"}),
            }
        })


def _task(max_pages=3):
    return {
        "name": "paging",
        "type": "api",
        "url": "https://api.x/y?limit=10&rec_tag=",
        "pagination": {
            "offset_param": "from",
            "limit": 10,
            "max_pages": max_pages,
            "session_param": "session_id",
            "session_from": "data.next_page",
        },
        "outputs": [
            {
                "target_api": {"url": "https://horgrix.com/x/batch"},
                "parser": {
                    "type": "json",
                    "root_path": "data.list",
                    "fields": [
                        {"name": "app_id", "path": "app_id", "to_number": True},
                        {"name": "position", "index": 1, "to_number": True},
                    ],
                },
            }
        ],
    }


def _run(task):
    source = _PagingSource()
    registry = SourceRegistry()
    registry.register("api", source)
    engine = CrawlerEngine(registry)

    payloads = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"code": 0}'

    def fake_urlopen(req, timeout):
        payloads.append(json.loads(req.data.decode("utf-8")))
        return _Resp()

    db = Database(":memory:")
    db.init_system_tables()
    with mock.patch("crawler.sinks.api_sink.urllib.request.urlopen", fake_urlopen):
        stats = asyncio.run(engine.run(task, db))
    return source, payloads, stats


class TestSessionPaging:
    def test_fetches_all_pages_with_same_session(self):
        source, _, stats = _run(_task())
        assert stats["api_sent"] == 30
        # 第 1 页不带 from，后续页带 from + session_id
        assert "from=" not in source.calls[0]
        assert "from=10" in source.calls[1] and "session_id=SID-1" in source.calls[1]
        assert "from=20" in source.calls[2] and "session_id=SID-1" in source.calls[2]

    def test_positions_continue_across_pages(self):
        _, payloads, _ = _run(_task())
        positions = [row["position"] for p in payloads for row in p["rows"]]
        assert positions == list(range(1, 31))
        assert len(set(positions)) == 30

    def test_stops_when_next_page_missing(self):
        """第 3 页响应没有 next_page → 不会多翻第 4 页。"""
        source, _, _ = _run(_task(max_pages=5))
        assert len(source.calls) == 3

    def test_without_pagination_single_request(self):
        task = _task()
        task.pop("pagination")
        source, payloads, stats = _run(task)
        assert len(source.calls) == 1
        assert stats["api_sent"] == 10
        assert [r["position"] for r in payloads[0]["rows"]] == list(range(1, 11))
