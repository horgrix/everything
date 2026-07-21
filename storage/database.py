"""
SQLite 数据库管理模块
提供连接管理、系统表初始化、业务表动态创建、UPSERT等核心操作。
"""

import sqlite3
import hashlib
import os
import threading
from typing import Optional, Any

# schema.sql 的路径（相对于本文件）
_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


class Database:
    """SQLite 数据库连接管理器，线程安全"""

    def __init__(self, db_path: str = "crawler.db"):
        self._db_path = db_path
        self._local = threading.local()

    @property
    def conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接（自动创建）"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA foreign_keys = ON")
            self._local.conn.execute("PRAGMA journal_mode = WAL")
            self._local.conn.execute("PRAGMA busy_timeout = 5000")
        return self._local.conn

    def init_system_tables(self):
        """执行 schema.sql 初始化系统元数据表"""
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            self.conn.executescript(f.read())
        self.conn.commit()

    # ================================================================
    # 业务表动态创建
    # ================================================================

    def ensure_business_table(self, table_name: str, columns: list[dict], indexes: list[dict] = None):
        """
        根据 YAML 配置的 columns 和 indexes 动态创建业务表。

        参数:
            table_name: 业务表名称
            columns: 列定义列表，每项格式 {"name": "col", "type": "TEXT", "constraint": "NOT NULL UNIQUE"}
            indexes: 索引定义列表，格式 [{"name": "idx_xx", "columns": ["a","b"], "unique": true}]
        """
        if indexes is None:
            indexes = []

        # 1. 建表
        col_defs = []
        for col in columns:
            parts = [col["name"], col["type"]]
            if "constraint" in col:
                parts.append(col["constraint"])
            col_defs.append(" ".join(parts))

        create_sql = (
            f"CREATE TABLE IF NOT EXISTS {table_name} (\n"
            f"  {', \n  '.join(col_defs)}\n"
            f")"
        )
        self.conn.execute(create_sql)

        # 2. 获取已有索引
        existing = self._get_existing_indexes(table_name)

        # 3. 建索引
        for idx in indexes:
            idx_name = idx["name"]
            if idx_name in existing:
                continue
            unique = "UNIQUE" if idx.get("unique", False) else ""
            col_list = ", ".join(idx["columns"])
            idx_sql = (
                f"CREATE {unique} INDEX IF NOT EXISTS {idx_name} "
                f"ON {table_name} ({col_list})"
            )
            self.conn.execute(idx_sql)

        self.conn.commit()

    def _get_existing_indexes(self, table_name: str) -> set:
        """查询某表已有的索引名称集合"""
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
            (table_name,),
        ).fetchall()
        return {row["name"] for row in rows}

    # ================================================================
    # 系统表操作
    # ================================================================

    def upsert_task(self, task_name: str, task_type: str, target_table: str,
                    schedule: str, config_yaml: str) -> int:
        """
        插入或更新任务定义，返回 task_id。
        """
        sql = """
            INSERT INTO crawl_tasks (task_name, task_type, target_table, schedule, config_yaml)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(task_name) DO UPDATE SET
                task_type   = excluded.task_type,
                target_table = excluded.target_table,
                schedule    = excluded.schedule,
                config_yaml = excluded.config_yaml,
                updated_at  = excluded.updated_at
        """
        cur = self.conn.execute(sql, (
            task_name, task_type, target_table, schedule, config_yaml
        ))
        self.conn.commit()
        # 返回 task_id：insert 用 lastrowid，update 需回查
        if cur.lastrowid != 0:
            return cur.lastrowid
        row = self.conn.execute(
            "SELECT id FROM crawl_tasks WHERE task_name=?", (task_name,)
        ).fetchone()
        return row["id"] if row else 0

    def get_enabled_tasks(self) -> list[sqlite3.Row]:
        """获取所有启用的任务"""
        return self.conn.execute(
            "SELECT * FROM crawl_tasks WHERE enabled=1"
        ).fetchall()

    def get_task_by_name(self, task_name: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM crawl_tasks WHERE task_name=?", (task_name,)
        ).fetchone()

    def start_crawl_log(self, task_id: int) -> int:
        """创建一条 running 状态的日志，返回 log_id"""
        cur = self.conn.execute(
            "INSERT INTO crawl_logs (task_id) VALUES (?)", (task_id,)
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_crawl_log(self, log_id: int, records_new: int, records_updated: int,
                         records_skipped: int, error_msg: str = None, duration_ms: int = None):
        """更新运行日志为完成状态"""
        self.conn.execute(
            """UPDATE crawl_logs SET status='success',
               records_new=?, records_updated=?, records_skipped=?,
               error_msg=?, duration_ms=?
               WHERE id=?""",
            (records_new, records_updated, records_skipped, error_msg, duration_ms, log_id),
        )
        self.conn.commit()

    def fail_crawl_log(self, log_id: int, error_msg: str, duration_ms: int = None):
        """标记运行日志为失败"""
        self.conn.execute(
            "UPDATE crawl_logs SET status='failed', error_msg=?, duration_ms=? WHERE id=?",
            (error_msg, duration_ms, log_id),
        )
        self.conn.commit()

    # ================================================================
    # 去重与内容变更检测
    # ================================================================

    def check_dedup(self, url: str, task_id: int, target_table: str) -> Optional[dict]:
        """
        查询去重状态。
        返回 None 表示新URL，否则返回 {"record_id": int, "content_hash": str}。
        """
        url_hash = self._hash(url)
        row = self.conn.execute(
            "SELECT record_id, content_hash FROM dedup_log WHERE url_hash=?",
            (url_hash,),
        ).fetchone()
        if row is None:
            return None
        return {"record_id": row["record_id"], "content_hash": row["content_hash"]}

    def upsert_dedup(self, url: str, task_id: int, target_table: str,
                     record_id: int, content_hash: str):
        """
        UPSERT 去重表：新URL插入，已有URL更新 hit_count 和 content_hash。
        """
        url_hash = self._hash(url)
        sql = """
            INSERT INTO dedup_log (url_hash, url, task_id, target_table, record_id, content_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url_hash) DO UPDATE SET
                last_seen    = datetime('now', 'localtime'),
                record_id    = excluded.record_id,
                content_hash = excluded.content_hash,
                hit_count    = dedup_log.hit_count + 1
        """
        self.conn.execute(sql, (url_hash, url, task_id, target_table, record_id, content_hash))
        self.conn.commit()

    # ================================================================
    # 业务数据写入
    # ================================================================

    def insert_business_record(self, table_name: str, data: dict) -> int:
        """向业务表插入一条记录。返回 lastrowid。"""
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?" for _ in data])
        sql = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"
        cur = self.conn.execute(sql, list(data.values()))
        self.conn.commit()
        return cur.lastrowid

    def update_business_record(self, table_name: str, record_id: int,
                                data: dict, id_column: str = "id"):
        """按主键更新业务表记录"""
        sets = ", ".join([f"{k}=?" for k in data.keys()])
        values = list(data.values()) + [record_id]
        self.conn.execute(
            f"UPDATE {table_name} SET {sets} WHERE {id_column}=?",
            values,
        )
        self.conn.commit()

    def insert_business_records_batch(self, table_name: str,
                                       rows: list[dict]) -> dict:
        """
        批量 UPSERT 写入业务表。

        在一个事务内逐行执行 INSERT ... ON CONFLICT ... DO UPDATE，
        通过前后行数对比统计新增/更新数量。

        参数:
            table_name: 业务表名称
            rows: 待写入的数据列表

        返回:
            {"inserted": int, "updated": int}
        """
        if not rows:
            return {"inserted": 0, "updated": 0}

        columns = list(rows[0].keys())
        col_names = ", ".join(columns)
        placeholders = ", ".join(["?" for _ in columns])

        # 构建 SET 子句：排除 id 列（主键），其余用 excluded.xxx
        update_cols = [c for c in columns if c != "id"]
        set_clause = ", ".join([f"{c}=excluded.{c}" for c in update_cols])

        sql = (
            f"INSERT INTO {table_name} ({col_names}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT DO UPDATE SET {set_clause}"
        )

        before_count = self.conn.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0]

        with self.conn:  # 事务自动 commit/rollback
            for row in rows:
                values = [row.get(c) for c in columns]
                self.conn.execute(sql, values)

        after_count = self.conn.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0]

        delta = after_count - before_count
        return {
            "inserted": max(0, delta),
            "updated": max(0, len(rows) - delta),
        }

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def hash_content(data: dict) -> str:
        """对结构化数据 dict 做内容哈希（排序后JSON序列化再哈希，保证一致性）"""
        import json
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None