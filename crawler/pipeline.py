"""
Data pipeline: parse → clean → filter → inject → write.

Extracted from CrawlerEngine so the engine only orchestrates
while the pipeline handles all data transformation.
"""

from dataclasses import dataclass, field
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from .parser import Parser
from .cleaner import Cleaner
from .template import URLTemplate

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of processing one output target."""
    inserted: int = 0
    updated: int = 0
    total: int = 0
    api_sent: int = 0
    api_failed: int = 0

    def __add__(self, other: "PipelineResult") -> "PipelineResult":
        return PipelineResult(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            total=self.total + other.total,
            api_sent=self.api_sent + other.api_sent,
            api_failed=self.api_failed + other.api_failed,
        )


class DataPipeline:
    """
    Stateless data transformation pipeline.

    Takes raw data from a DataSource and an output config,
    runs the full chain: parse → clean → filter → inject → upsert.

    Usage:
        pipeline = DataPipeline(parser, cleaner, db)
        result = pipeline.process(raw_data, output_config, context)
    """

    def __init__(self, parser: Parser = None, cleaner: Cleaner = None):
        self._parser = parser or Parser()
        self._cleaner = cleaner or Cleaner()

    def process(
        self,
        raw_data: Any,
        output_config: dict,
        db,
        context: dict,
        collect_rows: list | None = None,
    ) -> PipelineResult:
        """
        Process one output target against raw data.

        Steps:
          1. Ensure target table exists (only when target_table present)
          2. Extract page-level elements (element_selector)
          3. Parse raw data into rows
          4. Clean & filter rows
          5. Inject source_url
          6. Batch UPSERT into database (only when target_table present)
          7. POST rows to remote API (only when target_api present)

        Args:
            collect_rows: 批量上传模式。传入一个 list 时，清洗后的 rows
                会被 extend 进去，而不是立即 POST（由调用方统一 flush）。
                为 None 时保持逐次立即上传（向后兼容）。

        Returns:
            PipelineResult with inserted/updated/total/api_sent/api_failed counts.
        """
        table = output_config.get("target_table", "")
        target_api = output_config.get("target_api")
        parser_config = output_config.get("parser", {})
        parser_fields = parser_config.get("fields", [])
        table_schema = output_config.get("table_schema", {})

        # 1. Ensure business table exists（仅 target_table）
        if table and table_schema:
            db.ensure_business_table(
                table,
                table_schema.get("columns", []),
                table_schema.get("indexes", []),
            )

        # 2. Extract page-level elements into context
        element_selector_config = parser_config.get("element_selector", {})
        if element_selector_config:
            element_vars = self._parser.extract_element_vars(
                raw_data, element_selector_config
            )
            context.update(element_vars)

        # 3. Parse
        parsed = self._parser.parse_rows(raw_data, parser_config, context=context)
        if not parsed:
            return PipelineResult()

        # 4. Clean & filter
        cleaned = self._cleaner.clean_batch(parsed, parser_fields)

        # 5. Inject source_url
        src_field_names = Cleaner.field_names(parser_fields)
        url = context.get("url", "")
        if "source_url" in src_field_names and url:
            for row in cleaned:
                if "source_url" not in row:
                    row["source_url"] = url

        result = PipelineResult(total=len(cleaned))

        # 6. Batch upsert（仅 target_table）
        if table:
            db_result = db.insert_business_records_batch(table, cleaned)
            result.inserted = db_result["inserted"]
            result.updated = db_result["updated"]

        # 7. POST to remote API（仅 target_api）
        if target_api:
            if collect_rows is not None:
                # 批量模式：累积 rows，由 engine 在循环结束后统一 flush
                collect_rows.extend(cleaned)
            elif self._post_to_api(target_api, cleaned, context):
                result.api_sent = len(cleaned)
            else:
                result.api_failed = len(cleaned)

        return result

    def flush_api(
        self,
        target_api_config: dict,
        rows: list[dict],
        context: dict,
    ) -> PipelineResult:
        """把攒批累积的 rows 统一 POST 到 target_api（一次批量上传）。

        Args:
            target_api_config: output 的 target_api 配置（含 url/method/headers）。
            rows: 累积的清洗后行数据。
            context: 用于解析 target_api url 中的模板变量。

        Returns:
            PipelineResult with api_sent/api_failed counts.
        """
        if not rows:
            return PipelineResult()
        if self._post_to_api(target_api_config, rows, context):
            return PipelineResult(api_sent=len(rows))
        return PipelineResult(api_failed=len(rows))

    def _post_to_api(
        self,
        target_api_config: dict,
        rows: list[dict],
        context: dict,
    ) -> bool:
        """把清洗后的 rows 通过 HTTP POST 到远程 API（body 固定 {"rows": [...]}）。"""
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
