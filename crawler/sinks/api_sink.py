"""
Remote HTTP API sink: POSTs cleaned rows to a remote endpoint.

Claims the ``target_api`` output key. The sink owns a per-output buffer so
batching is fully encapsulated here:

* ``batch_size`` unset (0) -> accumulate rows, upload in one request on
  ``flush()`` (end of run).
* ``batch_size: N`` -> upload as soon as N rows are buffered; the remainder
  is uploaded on ``flush()``.

Request body is fixed as ``{"rows": [...]}``; a response with ``code == 0``
counts as success (see docs/task_config_guide.md).
"""

import json
import logging
import urllib.error
import urllib.request

from .base import OutputSink, SinkResult
from ..template import URLTemplate

logger = logging.getLogger(__name__)


class ApiSink(OutputSink):
    """Remote HTTP output sink (key: ``target_api``)."""

    key = "target_api"

    def __init__(self):
        # id(output_config) -> (config_ref, rows). Keyed by object identity
        # because the engine reuses the same output dict across iterations.
        self._buffers: dict[int, tuple[dict, list[dict]]] = {}

    # ---- SQL-agnostic helpers ----

    @staticmethod
    def get_batch_size(output_config: dict) -> int:
        """Read ``target_api.batch_size`` (positive int); 0 / invalid -> 0."""
        target_api = output_config.get("target_api") or {}
        raw = target_api.get("batch_size", 0)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    def _buffer(self, output_config: dict) -> list[dict]:
        key = id(output_config)
        entry = self._buffers.get(key)
        if entry is None or entry[0] is not output_config:
            entry = (output_config, [])
            self._buffers[key] = entry
        return entry[1]

    # ---- OutputSink interface ----

    def prepare(self, output_config: dict, context: dict, db) -> None:
        # No remote setup needed; just make sure the buffer exists.
        if output_config.get("target_api"):
            self._buffer(output_config)

    def write(self, rows: list[dict], output_config: dict, context: dict, db) -> SinkResult:
        if not rows or not output_config.get("target_api"):
            return SinkResult()
        buffer = self._buffer(output_config)
        buffer.extend(rows)
        return self._drain_full_batches(output_config, context, buffer)

    def flush(self, output_config: dict, context: dict, db) -> SinkResult:
        if not output_config.get("target_api"):
            return SinkResult()
        buffer = self._buffer(output_config)
        if not buffer:
            return SinkResult()
        batch = list(buffer)
        buffer.clear()
        return self._send(output_config, batch, context)

    # ---- Internals ----

    def _drain_full_batches(
        self, output_config: dict, context: dict, buffer: list[dict]
    ) -> SinkResult:
        batch_size = self.get_batch_size(output_config)
        result = SinkResult()
        if batch_size <= 0:
            return result
        while len(buffer) >= batch_size:
            batch = buffer[:batch_size]
            del buffer[:batch_size]
            result += self._send(output_config, batch, context)
        return result

    def _send(self, output_config: dict, rows: list[dict], context: dict) -> SinkResult:
        target_api = output_config.get("target_api") or {}
        if self._post_to_api(target_api, rows, context):
            return SinkResult(sent=len(rows))
        return SinkResult(failed=len(rows))

    @staticmethod
    def _post_to_api(
        target_api_config: dict,
        rows: list[dict],
        context: dict,
    ) -> bool:
        """POST cleaned rows to the remote API (body ``{"rows": [...]}``)."""
        url = target_api_config.get("url", "")
        if not url:
            logger.error("target_api 缺少 url，跳过上传")
            return False

        url = URLTemplate.resolve(url, context=context)
        method = target_api_config.get("method", "POST")
        headers = {"Content-Type": "application/json"}
        headers.update(target_api_config.get("headers", {}))

        payload = json.dumps({"rows": rows}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers=headers, method=method
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read().decode("utf-8")
                status = resp.status
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            logger.error("target_api 上传失败 HTTP %s: %s", e.code, body)
            return False
        except urllib.error.URLError as e:
            logger.error("target_api 上传失败：%s", e.reason)
            return False

        logger.info(
            "target_api 上传 %d 行到 %s，HTTP %s: %s",
            len(rows), url, status, body,
        )
        try:
            result = json.loads(body)
        except json.JSONDecodeError:
            result = {}
        return result.get("code") == 0
