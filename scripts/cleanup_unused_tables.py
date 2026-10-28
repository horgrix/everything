#!/usr/bin/env python3
"""清理 crawler.db 里已经没有任何消费者的历史表。

判定"没用"的口径（逐张表在执行时现场复核，任一条件不满足就跳过）：
  1. 库里存在该表；
  2. 没有任何 config/tasks/**/*.yaml 的 outputs[].target_table 指向它（没人写）；
  3. 没有任何任务的 SQL（db.query）里出现该表名（没人读）；
  4. 代码里没有引用：everything/api、everything-ui/src、tests、storage、
     crawler、task_manager、scheduler、app.py、main.py、../everything-api；
  5. 不属于框架表（crawl_tasks / crawl_logs / dedup_log）。

用法：
  python scripts/cleanup_unused_tables.py                 # 预演（默认，不动库）
  python scripts/cleanup_unused_tables.py --emit-sql x.sql # 导出可评审的 DROP 脚本
  python scripts/cleanup_unused_tables.py --apply          # 真的删（先自动备份）
  python scripts/cleanup_unused_tables.py --apply --vacuum # 删完再压缩库文件

安全措施：--apply 会先把整库备份到 backups/crawler-YYYYmmdd-HHMMSS.db，
每个被删表的行数/最新时间写进同目录的 .json 日志；删除在单个事务里完成。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "crawler.db"
BACKUP_DIR = ROOT / "backups"

# 框架表：由 storage/schema.sql + storage/database.py 动态维护，不参与清理
FRAMEWORK_TABLES = {"crawl_tasks", "crawl_logs", "dedup_log"}

# 代码引用扫描范围（相对仓库根目录）
CODE_ROOTS = [
    "api", "app.py", "main.py", "static", "tests",
    "storage", "crawler", "task_manager", "scheduler", "scripts",
    "../everything-api", "../everything-ui/src",
]
CODE_EXTS = (".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".sql", ".yaml", ".yml")

# 候选清理清单：(表名, 分组, 说明)
CANDIDATES: list[tuple[str, str, str]] = [
    # ---- 广告 DWS 旧版本链：被 v4/v5 替代 ----
    ("dws_taptap_app_keywords_ad_v2_daily", "广告 DWS 旧版本链", "关键词广告每日计算已被 v4 替代"),
    ("dws_taptap_ad_aggr_v2_daily", "广告 DWS 旧版本链", "广告每日聚合 v2"),
    ("dws_taptap_ad_aggr_v3_daily", "广告 DWS 旧版本链", "广告每日聚合 v3"),
    ("dws_taptap_ad_aggr_v4_daily", "广告 DWS 旧版本链", "广告每日聚合 v4，现为 v5"),
    ("dws_taptap_ad_aggr_daily", "广告 DWS 旧版本链", "广告每日聚合 v1"),
    ("dws_taptap_ad_overview_v4_daily", "广告 DWS 旧版本链", "广告每日汇总 v4，现为 v5"),
    ("dws_taptap_ad_landing_pc_v2_daily", "广告 DWS 旧版本链", "PC首页广告 v2"),
    ("dws_taptap_ad_landing_pc_v3_daily", "广告 DWS 旧版本链", "PC首页广告 v3，现为 v4"),
    ("dws_taptap_ad_pc_landing_v2_daily", "广告 DWS 旧版本链", "PC首页广告旧命名"),
    ("dws_taptap_landing_ad_daily", "广告 DWS 旧版本链", "首页广告旧命名"),
    ("dws_taptap_app_keywords_ad_overview", "广告 DWS 旧版本链", "关键词广告汇总，任务已下线"),
    ("dws_taptap_app_landing_ad_overview", "广告 DWS 旧版本链", "App首页广告汇总，任务已下线"),
    ("dws_taptap_app_landing_ad_overview_hourly", "广告 DWS 旧版本链", "App首页广告每时汇总，任务已下线"),
    ("dws_taptap_app_landing_ad_monthly", "广告 DWS 旧版本链", "App首页广告每月，任务已下线"),
    ("dws_taptap_ad_rank", "广告 DWS 旧版本链", "广告排名旧表，现为 dws_taptap_ad_rank_v4_daily"),
    ("dws_taptap_app_keywords_ad_rank", "广告 DWS 旧版本链", "关键词广告排名旧表"),
    ("dws_taptap_app_landing_ad_rank", "广告 DWS 旧版本链", "App首页广告排名旧表"),
    # ---- 旧采集原始表：采集任务已删除 ----
    ("taptap_ad_loading_hourly", "旧采集原始表", "采集任务已删；且 crawled_at 存的是未渲染的 {now:...} 模板"),
    ("taptap_ad_loading_hourlysadasdsadadsadsadasdsa", "旧采集原始表", "手滑建的空表"),
    ("taptap_app_ios_ad_loading_hourly", "旧采集原始表", "采集任务已删"),
    ("taptap_ad_hourly", "旧采集原始表", "采集任务已删"),
    ("taptap_pc_top_ad_hourly", "旧采集原始表", "采集任务已删（现用 taptap_pc_ad_top_hourly）"),
    ("taptap_pc_played_ids_daily", "旧采集原始表", "采集任务已删"),
    ("taptap_pc_played_ids_hourly", "旧采集原始表", "采集任务已删"),
    ("taptap_pc_sell_ids_daily", "旧采集原始表", "采集任务已删"),
    ("taptap_app_sell_list_hourly", "旧采集原始表", "采集任务已删"),
    ("taptap_pc_sell_list_hourly", "旧采集原始表", "采集任务已删"),
    ("taptap_pc_ad_load", "旧采集原始表", "采集任务已删"),
    ("taptap_pc_hot_list_daily", "旧采集原始表", "采集任务已删"),
    ("taptap_rep_monthly", "旧采集原始表", "采集任务已删"),
    ("ios_games_downlod_daily", "旧采集原始表", "拼写错误的空表，无任务"),
    # ---- 其他 DWS / 临时表 ----
    ("dws_taptap_pc_peak_players_daily", "其他 DWS / 临时表", "在线人数旧日表"),
    ("dws_taptap_game_basic_info_monthly", "其他 DWS / 临时表", "游戏基础信息月表，已被 dws_taptap_game 取代"),
    ("dws_taptap_peak_players_hourly", "其他 DWS / 临时表", "峰值玩家旧表"),
    ("dws_tmp_taptap_peak_players_hourly", "其他 DWS / 临时表", "临时表"),
    ("dws_taptap_sell_daily", "其他 DWS / 临时表", "畅销榜旧表"),
    ("dws_taptap_sell_monthly", "其他 DWS / 临时表", "畅销榜旧表"),
    ("dws_taptap_sell_hourly", "其他 DWS / 临时表", "畅销榜旧表"),
    ("dws_taptap_pc_peak_online_players", "其他 DWS / 临时表", "无任务、无数据"),
    ("dws_taptap_pc_source_online_players", "其他 DWS / 临时表", "无任务、无数据"),
    ("dws_taptap_pc_source_games_online_players", "其他 DWS / 临时表", "无任务、无数据"),
    # ---- 火炬之光 / 心动小镇系列 ----
    ("dws_games_torchlight_download_daily", "火炬之光 / 心动小镇系列", "专项项目旧表"),
    ("dws_games_torchlight_ios_download_daily", "火炬之光 / 心动小镇系列", "专项项目旧表"),
    ("dws_games_torchlight_tap_download_daily", "火炬之光 / 心动小镇系列", "专项项目旧表"),
    ("dws_games_torchlight_online_players", "火炬之光 / 心动小镇系列", "专项项目旧表"),
    ("torchlight_season_steam_peak_players", "火炬之光 / 心动小镇系列", "无任务、无数据"),
    # 注意：下面这张表名里混了一个零宽字符 U+200C，和正常的
    # dws_games_heartopia_online_players_hourly 在库里并存；正常那张仍在用，不能删。
    ("dws_games_heartopia\u200c_online_players_hourly", "火炬之光 / 心动小镇系列",
     "表名误含零宽字符 U+200C，从未被写入"),
    # ---- 杂项 ----
    ("hello_api", "杂项", "docs 教程示例表，无任务（仅文档提及）"),
    ("dws_taptap_games", "杂项", "无任务、无数据"),
    ("dws_taptap_download_daily_temp", "杂项", "临时表"),
]

SELF_NAMES = {"cleanup_unused_tables.py", "cleanup_unused_tables.sql"}


def token_re(table: str) -> re.Pattern[str]:
    """表名整词匹配，避免 dws_x 命中 dws_x_monthly。"""
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(table) + r"(?![A-Za-z0-9_])")


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def table_stats(conn: sqlite3.Connection, table: str) -> tuple[int, str]:
    n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    last = ""
    for key in ("crawled_at", "created_at", "updated_at"):
        if key in cols:
            value = conn.execute(f'SELECT MAX("{key}") FROM "{table}"').fetchone()[0]
            last = str(value or "")
            break
    return n, last


def load_task_usage(tables: set[str]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """返回 (写入来源, 读取来源)。只扫配置，不连库。"""
    import yaml  # 局部导入：没装 pyyaml 时也能跑 --emit-sql

    writes: dict[str, list[str]] = {}
    reads: dict[str, list[str]] = {}
    for path in sorted(glob.glob(str(ROOT / "config/tasks/**/*.yaml"), recursive=True)):
        with open(path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        name = cfg.get("name") or os.path.relpath(path, ROOT)
        for out in cfg.get("outputs") or []:
            target = out.get("target_table")
            if target:
                writes.setdefault(target, []).append(name)
        query = (cfg.get("db") or {}).get("query") or ""
        if not query:
            continue
        for table in tables:
            if token_re(table).search(query):
                reads.setdefault(table, []).append(name)
    return writes, reads


def collect_code_files() -> list[Path]:
    files: list[Path] = []
    for root in CODE_ROOTS:
        path = (ROOT / root).resolve()
        if path.is_dir():
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = [
                    d for d in dirnames
                    if d not in {"node_modules", ".git", "dist", "__pycache__", ".venv", "backups"}
                ]
                for filename in filenames:
                    if filename.endswith(CODE_EXTS) and filename not in SELF_NAMES:
                        files.append(Path(dirpath) / filename)
        elif path.is_file() and path.name not in SELF_NAMES:
            files.append(path)
    return files


def find_code_refs(tables: set[str]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path in collect_code_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for table in tables:
            if token_re(table).search(text):
                hits.setdefault(table, []).append(
                    os.path.relpath(path, ROOT).replace("\\", "/")
                )
    return hits


def verify(conn: sqlite3.Connection, candidates: list[tuple[str, str, str]]):
    """返回 (可删列表, 跳过列表)。"""
    names = {name for name, _, _ in candidates}
    writes, reads = load_task_usage(names)
    code_refs = find_code_refs(names)

    droppable, skipped = [], []
    for table, group, note in candidates:
        reason = None
        if table in FRAMEWORK_TABLES:
            reason = "框架表"
        elif not table_exists(conn, table):
            reason = "表不存在"
        elif writes.get(table):
            reason = f"仍被任务写入：{', '.join(writes[table][:2])}"
        elif reads.get(table):
            reason = f"仍被任务读取：{', '.join(reads[table][:2])}"
        elif code_refs.get(table):
            reason = f"代码仍有引用：{', '.join(code_refs[table][:2])}"
        if reason:
            skipped.append((table, group, note, reason))
        else:
            droppable.append((table, group, note))
    return droppable, skipped


def emit_sql(path: Path, rows: list[tuple[str, int, str, str, str]]) -> None:
    groups: dict[str, list[tuple[str, int, str, str]]] = {}
    for table, n, last, group, note in rows:
        groups.setdefault(group, []).append((table, n, last, note))

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"-- crawler.db 历史表清理脚本",
        f"-- 生成时间：{stamp}",
        "-- 判定口径：config/tasks 无人写入、无人读取；代码（everything/api、everything-ui/src、",
        "--           tests、storage、crawler、task_manager、scheduler、../everything-api）无引用。",
        "-- 生成前先备份：sqlite3 crawler.db \".backup backups/crawler-<时间戳>.db\"",
        "-- 执行：sqlite3 crawler.db < scripts/cleanup_unused_tables.sql",
        "--",
        f"-- 共 {len(rows)} 张表",
        "",
        "PRAGMA foreign_keys = OFF;",
        "BEGIN;",
        "",
    ]
    for group, items in groups.items():
        lines.append(f"-- ===== {group}（{len(items)} 张）=====")
        for table, n, last, note in items:
            lines.append(f"-- {note}｜清理前行数 {n}｜最新数据 {last or '-'}")
            lines.append(f'DROP TABLE IF EXISTS "{table}";')
        lines.append("")
    lines += [
        "COMMIT;",
        "",
        "-- 回收磁盘空间（可选，单独事务，执行期间会重写整个库文件）",
        "-- VACUUM;",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def backup(conn: sqlite3.Connection) -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    target = BACKUP_DIR / f"crawler-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    dest = sqlite3.connect(target)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 crawler.db 里的历史无用表")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite 库路径")
    parser.add_argument("--apply", action="store_true", help="真的执行删除（默认只预演）")
    parser.add_argument("--vacuum", action="store_true", help="删除后 VACUUM 回收空间")
    parser.add_argument("--emit-sql", metavar="PATH", help="把 DROP 脚本导出到这个文件")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"库文件不存在：{db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        droppable, skipped = verify(conn, CANDIDATES)
        facts = []
        for table, group, note in droppable:
            n, last = table_stats(conn, table)
            facts.append((table, n, last, group, note))

        total_rows = sum(n for _, n, *_ in facts)
        print(f"库：{db_path}（{db_path.stat().st_size / 1024 / 1024:.1f} MB）")
        print(f"候选 {len(CANDIDATES)} 张 → 现场复核通过 {len(facts)} 张，"
              f"跳过 {len(skipped)} 张，合计 {total_rows} 行\n")

        if skipped:
            print("跳过（依据现场复核）：")
            for table, _group, _note, reason in skipped:
                print(f"  - {table}：{reason}")
            print()

        current_group = None
        for table, n, last, group, note in facts:
            if group != current_group:
                print(f"[{group}]")
                current_group = group
            print(f"  {table:<48} {n:>7} 行  最新={last or '-'}   {note}")

        if args.emit_sql:
            out = Path(args.emit_sql)
            emit_sql(out, facts)
            print(f"\nDROP 脚本已导出：{out}")

        if not args.apply:
            print("\n预演结束，未改动数据库。确认无误后加 --apply 执行。")
            return 0

        if not facts:
            print("没有可清理的表，退出。")
            return 0

        bak = backup(conn)
        print(f"\n已备份：{bak}（{bak.stat().st_size / 1024 / 1024:.1f} MB）")

        log_path = bak.with_suffix(".json")
        log_path.write_text(
            json.dumps(
                [{"table": t, "rows": n, "last": l, "group": g, "note": note}
                 for t, n, l, g, note in facts],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

        with conn:
            for table, *_ in facts:
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        print(f"已删除 {len(facts)} 张表，明细见 {log_path}")

        if args.vacuum:
            before = db_path.stat().st_size
            conn.execute("VACUUM")
            after = db_path.stat().st_size
            print(f"VACUUM 完成：{before / 1024 / 1024:.1f} MB → {after / 1024 / 1024:.1f} MB")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
