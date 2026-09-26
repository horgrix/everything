"""Tests for outputs target_api (remote HTTP output)."""

import json
import urllib.error
from unittest import mock

import pytest

from crawler.pipeline import DataPipeline
from crawler.parser import Parser
from crawler.cleaner import Cleaner
 

RAW = '{"data": {"list": [{"app": {"id": 1}}, {"app": {"id": 2}}]}}'


@pytest.fixture
def pipeline():
    return DataPipeline(parser=Parser(), cleaner=Cleaner())


def _output_config(*, target_table=False, target_api=False):
    config = {
        "parser": {
            "type": "json",
            "root_path": "data.list",
            "fields": [{"name": "app_id", "path": "app.id", "to_number": True}],
        },
    }
    if target_table:
        config["target_table"] = "test_target_api_table"
        config["table_schema"] = {
            "columns": [
                {"name": "id", "type": "INTEGER", "constraint": "PRIMARY KEY AUTOINCREMENT"},
                {"name": "app_id", "type": "INTEGER"},
            ],
        }
    if target_api:
        config["target_api"] = {"url": "https://horgrix.com/api/data/t/rows/batch"}
    return config


class _FakeResponse:
    def __init__(self, body: str, status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body.encode("utf-8")


def test_api_only_success(pipeline):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["method"] = req.get_method()
        return _FakeResponse(json.dumps({"code": 0}))

    with mock.patch("crawler.pipeline.urllib.request.urlopen", fake_urlopen):
        result = pipeline.process(RAW, _output_config(target_api=True), None, {})

    assert result.api_sent == 2
    assert result.api_failed == 0
    assert result.inserted == 0  # 无本地表，不写库
    assert captured["url"] == "https://horgrix.com/api/data/t/rows/batch"
    assert captured["method"] == "POST"
    body = json.loads(captured["data"].decode("utf-8"))
    assert body["rows"] == [{"app_id": 1}, {"app_id": 2}]


def test_api_only_failure(pipeline):
    def fake_urlopen(req, timeout):
        raise urllib.error.URLError("boom")

    with mock.patch("crawler.pipeline.urllib.request.urlopen", fake_urlopen):
        result = pipeline.process(RAW, _output_config(target_api=True), None, {})

    assert result.api_sent == 0
    assert result.api_failed == 2


def test_coexist_table_and_api(pipeline, db):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["data"] = req.data
        return _FakeResponse(json.dumps({"code": 0}))

    with mock.patch("crawler.pipeline.urllib.request.urlopen", fake_urlopen):
        result = pipeline.process(
            RAW, _output_config(target_table=True, target_api=True), db, {}
        )

    # 本地库写入
    assert result.inserted == 2
    rows = db.conn.execute(
        "SELECT app_id FROM test_target_api_table ORDER BY app_id"
    ).fetchall()
    assert [r["app_id"] for r in rows] == [1, 2]
    # 远程上传
    assert result.api_sent == 2
    body = json.loads(captured["data"].decode("utf-8"))
    assert body["rows"] == [{"app_id": 1}, {"app_id": 2}]


def test_table_only_no_api(pipeline, db):
    """回归：仅 target_table 时行为不变。"""
    result = pipeline.process(RAW, _output_config(target_table=True), db, {})
    assert result.inserted == 2
    assert result.api_sent == 0
    assert result.api_failed == 0
