"""Tests for outputs target_api (remote HTTP output)."""

import json
import urllib.error
from unittest import mock

import pytest

from crawler.pipeline import DataPipeline
from crawler.parser import Parser
from crawler.cleaner import Cleaner
from crawler.sinks.api_sink import ApiSink
 

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

    with mock.patch("crawler.sinks.api_sink.urllib.request.urlopen", fake_urlopen):
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

    with mock.patch("crawler.sinks.api_sink.urllib.request.urlopen", fake_urlopen):
        result = pipeline.process(RAW, _output_config(target_api=True), None, {})

    assert result.api_sent == 0
    assert result.api_failed == 2


def test_coexist_table_and_api(pipeline, db):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["data"] = req.data
        return _FakeResponse(json.dumps({"code": 0}))

    with mock.patch("crawler.sinks.api_sink.urllib.request.urlopen", fake_urlopen):
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


def test_sink_buffers_until_flush():
    """sink 缓冲：write 不立即 POST，flush 才一次性发送。

    （原 pipeline.process(collect_rows=...) 延迟上传的等价断言。）
    """
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req)
        return _FakeResponse(json.dumps({"code": 0}))

    sink = ApiSink()
    output_config = _output_config(target_api=True)
    rows = [{"app_id": 1}, {"app_id": 2}]

    with mock.patch("crawler.sinks.api_sink.urllib.request.urlopen", fake_urlopen):
        written = sink.write(rows, output_config, {}, None)
        assert calls == []  # 未 flush 前不发请求
        assert written.sent == 0
        flushed = sink.flush(output_config, {}, None)

    assert len(calls) == 1
    assert flushed.sent == 2
    assert flushed.failed == 0
    body = json.loads(calls[0].data.decode("utf-8"))
    assert body["rows"] == rows


def test_flush_batches_buffered_rows_once():
    """多次 write 累积后 flush 合并成一次 POST。"""
    captured = {}

    def fake_urlopen(req, timeout):
        captured["data"] = req.data
        return _FakeResponse(json.dumps({"code": 0}))

    sink = ApiSink()
    output_config = _output_config(target_api=True)
    rows = [{"app_id": 1}, {"app_id": 2}, {"app_id": 3}]

    with mock.patch("crawler.sinks.api_sink.urllib.request.urlopen", fake_urlopen):
        sink.write(rows[:1], output_config, {}, None)
        sink.write(rows[1:], output_config, {}, None)
        result = sink.flush(output_config, {}, None)

    assert result.sent == 3
    assert result.failed == 0
    body = json.loads(captured["data"].decode("utf-8"))
    assert body["rows"] == rows


def test_flush_empty_buffer_no_request():
    """空缓冲 flush 不发起请求。"""
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req)
        return _FakeResponse(json.dumps({"code": 0}))

    sink = ApiSink()
    output_config = _output_config(target_api=True)
    with mock.patch("crawler.sinks.api_sink.urllib.request.urlopen", fake_urlopen):
        result = sink.flush(output_config, {}, None)

    assert calls == []
    assert result.sent == 0
    assert result.failed == 0


def test_flush_failure_counts_failed():
    """上传失败时计入 failed。"""
    def fake_urlopen(req, timeout):
        raise urllib.error.URLError("boom")

    sink = ApiSink()
    output_config = _output_config(target_api=True)
    with mock.patch("crawler.sinks.api_sink.urllib.request.urlopen", fake_urlopen):
        sink.write([{"app_id": 1}], output_config, {}, None)
        result = sink.flush(output_config, {}, None)

    assert result.sent == 0
    assert result.failed == 1


def test_batch_size_drains_full_batches():
    """batch_size=2：write 满批立即 POST，flush 发剩余。"""
    payloads = []

    def fake_urlopen(req, timeout):
        payloads.append(json.loads(req.data.decode("utf-8")))
        return _FakeResponse(json.dumps({"code": 0}))

    sink = ApiSink()
    output_config = _output_config(target_api=True)
    output_config["target_api"]["batch_size"] = 2

    with mock.patch("crawler.sinks.api_sink.urllib.request.urlopen", fake_urlopen):
        first = sink.write(
            [{"app_id": 1}, {"app_id": 2}, {"app_id": 3}], output_config, {}, None
        )
        assert first.sent == 2          # 写时已发满批
        assert len(payloads) == 1
        rest = sink.flush(output_config, {}, None)

    assert rest.sent == 1
    assert [p["rows"] for p in payloads] == [
        [{"app_id": 1}, {"app_id": 2}],
        [{"app_id": 3}],
    ]
