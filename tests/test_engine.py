"""Tests for CrawlerEngine orchestration (batched target_api upload)."""

import asyncio
import json
from unittest import mock

import pytest

from crawler.engine import CrawlerEngine
from crawler.sources.base import SourceRegistry, DataSource
from storage.database import Database


class _FakeSource(DataSource):
    """每次 fetch 返回一条记录，app_id 取自 context 的 iterate 变量。"""

    async def fetch(self, task_config, context):
        return [{"app_id": context.get("app_id")}]


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b'{"code": 0}'


def _make_task(n_apps: int, batch_size=None):
    target_api = {"url": "https://horgrix.com/x/batch"}
    if batch_size is not None:
        target_api["batch_size"] = batch_size
    return {
        "name": "t",
        "type": "api",
        "url": "http://x/{app_id}",
        "iterate": [{"var_name": "app_id", "values": list(range(1, n_apps + 1))}],
        "outputs": [
            {
                "target_api": target_api,
                "parser": {
                    "type": "sdk_mapping",
                    "fields": [
                        {"name": "app_id", "source": "app_id", "to_number": True},
                    ],
                },
            }
        ],
    }


def _run(engine, task, payloads):
    def fake_urlopen(req, timeout):
        payloads.append(json.loads(req.data.decode("utf-8")))
        return _FakeResponse()

    db = Database(":memory:")
    db.init_system_tables()
    with mock.patch("crawler.pipeline.urllib.request.urlopen", fake_urlopen):
        return asyncio.run(engine.run(task, db))


@pytest.fixture
def engine():
    registry = SourceRegistry()
    registry.register("api", _FakeSource())
    return CrawlerEngine(registry)


class TestGetBatchSize:
    def test_default_zero(self, engine):
        assert engine._get_batch_size({}) == 0
        assert engine._get_batch_size({"target_api": {"url": "x"}}) == 0

    def test_positive(self, engine):
        assert engine._get_batch_size({"target_api": {"batch_size": 200}}) == 200

    def test_invalid(self, engine):
        assert engine._get_batch_size({"target_api": {"batch_size": "abc"}}) == 0
        assert engine._get_batch_size({"target_api": {"batch_size": -1}}) == -1


class TestBatchedUpload:
    def test_no_batch_size_flushes_once(self, engine):
        """未配置 batch_size：循环结束统一 POST 一次。"""
        payloads = []
        stats = _run(engine, _make_task(3), payloads)
        assert len(payloads) == 1
        assert payloads[0]["rows"] == [
            {"app_id": 1}, {"app_id": 2}, {"app_id": 3},
        ]
        assert stats["api_sent"] == 3

    def test_batch_size_splits_uploads(self, engine):
        """batch_size=2，5 条数据 → 3 次 POST（2+2+1）。"""
        payloads = []
        stats = _run(engine, _make_task(5, batch_size=2), payloads)
        assert len(payloads) == 3
        assert payloads[0]["rows"] == [{"app_id": 1}, {"app_id": 2}]
        assert payloads[1]["rows"] == [{"app_id": 3}, {"app_id": 4}]
        assert payloads[2]["rows"] == [{"app_id": 5}]
        assert stats["api_sent"] == 5

    def test_batch_size_exact_multiple(self, engine):
        """batch_size=2，4 条数据恰好整除 → 2 次 POST，无剩余 flush。"""
        payloads = []
        stats = _run(engine, _make_task(4, batch_size=2), payloads)
        assert len(payloads) == 2
        assert payloads[0]["rows"] == [{"app_id": 1}, {"app_id": 2}]
        assert payloads[1]["rows"] == [{"app_id": 3}, {"app_id": 4}]
        assert stats["api_sent"] == 4
