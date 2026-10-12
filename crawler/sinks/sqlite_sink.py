"""
SQLite sink: writes cleaned rows to the local business table.

Claims the ``target_table`` output key. Behavior is identical to the
pre-refactor hard-coded path: ``ensure_business_table`` followed by
``insert_business_records_batch``.
"""

import logging

from .base import OutputSink, SinkResult

logger = logging.getLogger(__name__)


class SqliteSink(OutputSink):
    """Local SQLite business-table sink (key: ``target_table``)."""

    key = "target_table"

    def prepare(self, output_config: dict, context: dict, db) -> None:
        table = output_config.get("target_table")
        schema = output_config.get("table_schema") or {}
        if table and schema:
            db.ensure_business_table(
                table,
                schema.get("columns", []),
                schema.get("indexes", []),
            )

    def write(self, rows: list[dict], output_config: dict, context: dict, db) -> SinkResult:
        table = output_config.get("target_table")
        if not table or not rows:
            return SinkResult()
        db_result = db.insert_business_records_batch(table, rows)
        return SinkResult(
            inserted=db_result["inserted"],
            updated=db_result["updated"],
        )

    def flush(self, output_config: dict, context: dict, db) -> SinkResult:
        # SQLite writes happen immediately, nothing to flush.
        return SinkResult()
