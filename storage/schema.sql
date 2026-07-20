-- 爬虫系统数据库 Schema
-- SQLite 3.24.0+

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

-- ============================================================
-- 系统元数据表
-- ============================================================

-- 任务定义表
CREATE TABLE IF NOT EXISTS crawl_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name     TEXT NOT NULL UNIQUE,
    task_type     TEXT NOT NULL DEFAULT 'web',
    target_table  TEXT NOT NULL,
    schedule      TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    config_yaml   TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- URL去重追踪表
CREATE TABLE IF NOT EXISTS dedup_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash      TEXT NOT NULL UNIQUE,
    url           TEXT NOT NULL,
    task_id       INTEGER NOT NULL,
    record_id     INTEGER,
    target_table  TEXT NOT NULL,
    first_seen    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_seen     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    content_hash  TEXT,
    hit_count     INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (task_id) REFERENCES crawl_tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dedup_task ON dedup_log(task_id);
CREATE INDEX IF NOT EXISTS idx_dedup_record ON dedup_log(target_table, record_id);

-- 运行日志表
CREATE TABLE IF NOT EXISTS crawl_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         INTEGER NOT NULL,
    run_time        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    status          TEXT NOT NULL DEFAULT 'running',
    records_new     INTEGER NOT NULL DEFAULT 0,
    records_updated INTEGER NOT NULL DEFAULT 0,
    records_skipped INTEGER NOT NULL DEFAULT 0,
    error_msg       TEXT,
    duration_ms     INTEGER,
    FOREIGN KEY (task_id) REFERENCES crawl_tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_logs_task ON crawl_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_logs_time ON crawl_logs(run_time);