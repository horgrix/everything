"""
Remote MySQL sink: writes cleaned rows to a MySQL table.

Claims the ``target_mysql`` output key.

Design notes
------------
* **No SQL dialect mapping.** ``table_schema.columns`` ``type`` /
  ``constraint`` are written by the user in native MySQL dialect and are
  spliced verbatim into ``CREATE TABLE``. This sink never translates
  SQLite types to MySQL (or vice versa).
* ``password`` supports ``${ENV_VAR}`` substitution via the shared
  :func:`crawler.template.resolve_env_vars`.
* Blocking pymysql calls are moved off the event loop by the base
  ``*_async`` facades (``asyncio.to_thread``).

Example config::

    outputs:
      - target_mysql: "my_remote_table"
        mysql:
          host: "db.example.com"
          port: 3306
          user: "crawler"
          password: "${MYSQL_PASSWORD}"
          database: "analytics"
          charset: "utf8mb4"
          batch_size: 500        # optional; 0 -> flush once at end of run
        table_schema:            # native MySQL dialect, used verbatim
          columns:
            - name: id
              type: BIGINT
              constraint: AUTO_INCREMENT PRIMARY KEY
            - name: app_id
              type: BIGINT
            - name: name
              type: VARCHAR(255)
          indexes:
            - name: idx_app_id
              columns: [app_id]
              unique: true
        parser: {...}
"""

import asyncio
import logging

from .base import OutputSink, SinkResult
from ..mysql_client import build_mysql_connection
from ..template import resolve_env_vars

logger = logging.getLogger(__name__)


# ======================================================================
# Pure SQL builders (unit-testable without a live database)
# ======================================================================

def build_create_table_sql(table: str, columns: list[dict]) -> str:
    """Build ``CREATE TABLE IF NOT EXISTS`` using raw column definitions.

    ``type`` and ``constraint`` are taken verbatim — this project performs
    no SQL dialect translation.
    """
    col_defs = []
    for col in columns:
        name = col.get("name")
        col_type = col.get("type")
        if not name or not col_type:
            raise ValueError(
                f"表 '{table}' 的列定义缺少 name 或 type 字段: {col}"
            )
        parts = [str(name), str(col_type)]
        if col.get("constraint"):
            parts.append(str(col["constraint"]))
        col_defs.append(" ".join(parts))

    body = ", \n  ".join(col_defs)
    return f"CREATE TABLE IF NOT EXISTS {table} (\n  {body}\n)"


def build_index_statements(table: str, indexes: list[dict] | None) -> list[dict]:
    """Build ``CREATE [UNIQUE] INDEX`` statements (executed after create).

    Returns a list of ``{"name", "sql"}`` so callers can skip existing
    indexes before executing.
    """
    statements = []
    for idx in indexes or []:
        idx_name = idx["name"]
        unique = "UNIQUE " if idx.get("unique", False) else ""
        col_list = ", ".join(str(c) for c in idx["columns"])
        sql = (
            f"CREATE {unique}INDEX {idx_name} "
            f"ON {table} ({col_list})"
        )
        statements.append({"name": idx_name, "sql": sql})
    return statements


def suppressed_update_columns(table_schema: dict | None) -> set[str]:
    """Columns excluded from the ``ON DUPLICATE KEY UPDATE`` clause.

    Primary-key and auto-increment columns must not be overwritten. They are
    detected from the native ``constraint`` text written by the user.
    """
    suppressed: set[str] = set()
    for col in (table_schema or {}).get("columns", []):
        name = col.get("name")
        constraint = str(col.get("constraint", "")).upper()
        if not name:
            continue
        if "PRIMARY KEY" in constraint or "AUTO_INCREMENT" in constraint:
            suppressed.add(name)
    return suppressed


def build_upsert_sql(
    table: str,
    columns: list[str],
    suppress: set[str] | None = None,
) -> str:
    """Build a parameterized MySQL UPSERT statement (``%s`` placeholders).

    ``INSERT INTO t (cols) VALUES (%s, ...) ON DUPLICATE KEY UPDATE
    c = VALUES(c)``. Columns in ``suppress`` (primary key / auto-increment)
    are omitted from the UPDATE clause. When every column is suppressed,
    falls back to ``INSERT IGNORE``.
    """
    suppress = suppress or set()
    col_names = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))

    update_cols = [c for c in columns if c not in suppress]
    if not update_cols:
        return f"INSERT IGNORE INTO {table} ({col_names}) VALUES ({placeholders})"

    set_clause = ", ".join([f"{c}=VALUES({c})" for c in update_cols])
    return (
        f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {set_clause}"
    )


# ======================================================================
# Sink
# ======================================================================

class MysqlSink(OutputSink):
    """Remote MySQL output sink (key: ``target_mysql``)."""

    key = "target_mysql"

    def __init__(self):
        self._conns: dict[tuple, object] = {}
        self._prepared: set[int] = set()
        # id(output_config) -> (config_ref, rows)
        self._buffers: dict[int, tuple[dict, list[dict]]] = {}

    # ---- connection helpers ----

    @staticmethod
    def _conn_key(mysql_config: dict) -> tuple:
        return (
            str(mysql_config.get("host", "localhost")),
            int(mysql_config.get("port", 3306) or 3306),
            str(mysql_config.get("user", "")),
            str(mysql_config.get("password", "")),
            str(mysql_config.get("database", "")),
            str(mysql_config.get("charset", "utf8mb4")),
        )

    def _get_connection(self, output_config: dict):
        mysql_config = output_config.get("mysql") or {}
        cache_key = self._conn_key(mysql_config)
        conn = self._conns.get(cache_key)
        if conn is not None:
            return conn

        database = mysql_config.get("database", "")
        conn = build_mysql_connection(
            {
                **mysql_config,
                "password": resolve_env_vars(mysql_config.get("password", "")),
            },
            charset=mysql_config.get("charset", "utf8mb4"),
        )
        self._conns[cache_key] = conn
        logger.info(
            "MySQL 连接已建立: %s/%s",
            mysql_config.get("host", "localhost"), database,
        )
        return conn

    @staticmethod
    def _existing_indexes(conn, table: str) -> set[str]:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT INDEX_NAME FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (table,),
            )
            return {row["INDEX_NAME"] for row in cursor.fetchall()}

    def _buffer(self, output_config: dict) -> list[dict]:
        key = id(output_config)
        entry = self._buffers.get(key)
        if entry is None or entry[0] is not output_config:
            entry = (output_config, [])
            self._buffers[key] = entry
        return entry[1]

    @staticmethod
    def get_batch_size(output_config: dict) -> int:
        mysql_config = output_config.get("mysql") or {}
        raw = mysql_config.get("batch_size", 0)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    # ---- OutputSink interface ----

    def prepare(self, output_config: dict, context: dict, db) -> None:
        table = output_config.get("target_mysql")
        if not table:
            return
        self._buffer(output_config)

        cache_key = id(output_config)
        if cache_key in self._prepared:
            return

        conn = self._get_connection(output_config)
        schema = output_config.get("table_schema") or {}
        create_sql = build_create_table_sql(table, schema.get("columns", []))
        with conn.cursor() as cursor:
            cursor.execute(create_sql)
            existing = self._existing_indexes(conn, table)
            for stmt in build_index_statements(table, schema.get("indexes", [])):
                if stmt["name"] in existing:
                    continue
                cursor.execute(stmt["sql"])
        conn.commit()
        self._prepared.add(cache_key)
        logger.info("MySQL 业务表 '%s' 已就绪", table)

    def write(self, rows: list[dict], output_config: dict, context: dict, db) -> SinkResult:
        if not rows or not output_config.get("target_mysql"):
            return SinkResult()
        buffer = self._buffer(output_config)
        buffer.extend(rows)
        return self._drain_full_batches(output_config, buffer)

    def flush(self, output_config: dict, context: dict, db) -> SinkResult:
        if not output_config.get("target_mysql"):
            return SinkResult()
        buffer = self._buffer(output_config)
        if not buffer:
            return SinkResult()
        batch = list(buffer)
        buffer.clear()
        return self._send(output_config, batch)

    def close(self) -> None:
        for conn in self._conns.values():
            try:
                conn.close()
            except Exception:  # pragma: no cover - best effort cleanup
                pass
        self._conns.clear()
        self._prepared.clear()
        self._buffers.clear()

    # ---- Internals ----

    def _drain_full_batches(self, output_config: dict, buffer: list[dict]) -> SinkResult:
        batch_size = self.get_batch_size(output_config)
        result = SinkResult()
        if batch_size <= 0:
            return result
        while len(buffer) >= batch_size:
            batch = buffer[:batch_size]
            del buffer[:batch_size]
            result += self._send(output_config, batch)
        return result

    def _send(self, output_config: dict, rows: list[dict]) -> SinkResult:
        if not rows:
            return SinkResult()
        table = output_config["target_mysql"]
        schema = output_config.get("table_schema") or {}
        columns = list(rows[0].keys())
        sql = build_upsert_sql(table, columns, suppressed_update_columns(schema))

        conn = self._get_connection(output_config)
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) AS n FROM {table}")
                before = cursor.fetchone()["n"]
                cursor.executemany(
                    sql,
                    [[row.get(c) for c in columns] for row in rows],
                )
                cursor.execute(f"SELECT COUNT(*) AS n FROM {table}")
                after = cursor.fetchone()["n"]
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:  # pragma: no cover
                pass
            logger.error("target_mysql 写入失败 (%s): %s", table, e)
            return SinkResult(failed=len(rows))

        inserted = max(0, after - before)
        updated = max(0, len(rows) - inserted)
        return SinkResult(inserted=inserted, updated=updated)

    # ---- Async facades: offload blocking pymysql to a worker thread ----

    async def prepare_async(self, output_config, context, db) -> None:
        await asyncio.to_thread(self.prepare, output_config, context, db)

    async def write_async(self, rows, output_config, context, db) -> SinkResult:
        return await asyncio.to_thread(self.write, rows, output_config, context, db)

    async def flush_async(self, output_config, context, db) -> SinkResult:
        return await asyncio.to_thread(self.flush, output_config, context, db)

    async def close_async(self) -> None:
        await asyncio.to_thread(self.close)
