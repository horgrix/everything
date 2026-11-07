#!/usr/bin/env python3
"""把本地库查出来的 id 列表写进任务 YAML 的 iterate.values（只改那几行，别的一律不动）。

典型用法：任务需要遍历「当前小时出现过的 app_id」，但 iterate 只能写死值。
本脚本负责：查库 → 去重排序 → 只重写目标 var_name 下面的 values 列表。

    # 用表名 + 默认条件（crawled_at = 当前小时）
    python config/tasks/utils/script/fill_iterate_values.py \
        --yaml config/tasks/taptap-download/manual/TapPC热门游戏下载数据爬取任务.yaml \
        --var app_id \
        --table taptap_pc_hot_list_ids_hourly

    # 自定义 SQL（多表合并、复杂过滤都用这个）
    python config/tasks/utils/script/fill_iterate_values.py \
        --yaml config/tasks/taptap-download/manual/TapPC热门游戏下载数据爬取任务.yaml \
        --var app_id \
        --sql "SELECT DISTINCT app_id FROM taptap_pc_hot_list_ids_hourly
               WHERE crawled_at = strftime('%Y-%m-%d %H', 'now', 'localtime')
               ORDER BY app_id"

    # 先看会改成什么，不落盘
    ... --dry-run

与老写法（yaml.safe_load + safe_dump 整个文件重写）的区别：
  * 只替换目标 values 列表的几行，注释、URL、字段顺序、缩进、引号全部保留；
  * 两种列表风格都支持（`values:` 后缩进 / indentless sequence）；
  * 内容没变化时直接跳过，不刷新 mtime（避免每次跑都产生无意义 diff）；
  * 原子写（临时文件 + os.replace），中途失败不会留下半个文件。
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]   # config/tasks/utils/script → 项目根
DEFAULT_DB = PROJECT_ROOT / "crawler.db"
NOW_HOUR = "strftime('%Y-%m-%d %H', 'now', 'localtime')"

CRLF = "\r"
LF = "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="把库里查到的 id 列表写进任务 YAML 的 iterate.values")
    p.add_argument("--yaml", required=True, help="目标任务 YAML 路径")
    p.add_argument("--var", required=True, help="iterate 里的 var_name，如 app_id")
    p.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 库路径（默认项目根目录 crawler.db）")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--table", help="直接给表名，默认条件：列非空且 crawled_at = 当前小时")
    src.add_argument("--sql", help="自定义只读 SQL，取第一列作为值")
    p.add_argument("--column", default="app_id", help="--table 模式下参与去重的列（默认 app_id）")
    p.add_argument("--where", default=f"crawled_at = {NOW_HOUR}",
                   help="--table 模式下的附加条件（默认当前小时，传 \"\" 表示不加）")
    p.add_argument("--limit", type=int, default=0, help="最多取多少个（0 = 不限）")
    p.add_argument("--dry-run", action="store_true", help="只打印将要写入的内容，不改文件")
    return p.parse_args(argv)


def build_sql(args: argparse.Namespace) -> str:
    if args.sql:
        return args.sql
    where = f" WHERE {args.where}" if args.where else ""
    sql = f"SELECT DISTINCT {args.column} FROM {args.table}{where} ORDER BY {args.column}"
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"
    return sql


def fetch_values(db_path: Path, sql: str) -> list[str]:
    if not db_path.exists():
        raise SystemExit(f"库文件不存在：{db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    values = [str(r[0]) for r in rows if r[0] is not None and str(r[0]).strip() != ""]
    seen: set[str] = set()
    return [v for v in values if not (v in seen or seen.add(v))]


# 行尾允许带注释（如 `- var_name: "app_id"   # 说明`）
VAR_LINE = re.compile(
    r'^(?P<indent>\s*)-\s*var_name\s*:\s*'
    r'(?P<name>"[^"]*"|\'[^\']*\'|\S+?)(?:\s+#.*)?\s*$')
VALUES_LINE = re.compile(r"^(?P<indent>\s*)values\s*:(?:\s+#.*)?\s*$")


def _body(line: str) -> str:
    """去掉行尾换行符。"""
    return line.rstrip(CRLF + LF)


def _split_eol(line: str) -> str:
    """取出该行的换行符（保持原文件风格）。"""
    if line.endswith(CRLF + LF):
        return CRLF + LF
    if line.endswith(LF):
        return LF
    return ""


def rewrite_values(text: str, var_name: str, values: list[str]) -> tuple[str, int]:
    """只重写 var_name 对应的 values 列表，返回 (新文本, 被替换的旧条目数)。"""
    lines = text.splitlines(keepends=True)

    var_idx = None
    for i, line in enumerate(lines):
        m = VAR_LINE.match(_body(line))
        if m and m.group("name").strip("\"'") == var_name:
            var_idx = i
            break
    if var_idx is None:
        raise SystemExit(f"没找到 iterate 中 var_name: {var_name}"
                         "（检查 --var，或该任务是否真的配了 iterate）")

    var_indent = len(VAR_LINE.match(_body(lines[var_idx])).group("indent"))

    val_idx = None
    for j in range(var_idx + 1, len(lines)):
        raw = _body(lines[j])
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent <= var_indent:          # 走出了这个 iterate 项
            break
        if VALUES_LINE.match(raw):
            val_idx = j
            break
    if val_idx is None:
        raise SystemExit(f"var_name: {var_name} 下面没有 values: 列表")

    values_indent = len(VALUES_LINE.match(_body(lines[val_idx])).group("indent"))

    # 收集旧条目。两种风格都要支持：
    #     values:            values:
    #       - 1                - 1        ← indentless sequence（yaml.safe_dump 默认风格）
    start = val_idx + 1
    end = start
    item_indent = None
    while end < len(lines):
        raw = _body(lines[end])
        if not raw.strip() or raw.lstrip().startswith("#"):
            end += 1
            continue
        indent = len(raw) - len(raw.lstrip())
        is_item = raw.lstrip().startswith("- ")
        if item_indent is None:
            if not is_item:
                break                      # values: 下面是别的 key → 原来没有条目
            item_indent = indent
            end += 1
            continue
        if is_item and indent == item_indent:
            end += 1
            continue
        if not is_item and indent > item_indent:
            end += 1                       # 条目的续行
            continue
        break                              # 列表结束

    old_items = end - start
    if item_indent is None:
        item_indent = values_indent + 2

    eol = _split_eol(lines[val_idx]) or LF
    pad = " " * item_indent
    new_block = [f"{pad}- {v}{eol}" for v in values]
    return "".join(lines[: val_idx + 1] + new_block + lines[end:]), old_items


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    yaml_path = Path(args.yaml)
    if not yaml_path.exists():
        raise SystemExit(f"YAML 不存在：{yaml_path}")

    sql = build_sql(args)
    values = fetch_values(Path(args.db), sql)
    print(f"查询：{sql}")
    print(f"取到 {len(values)} 个值：{values[:10]}{' …' if len(values) > 10 else ''}")

    with open(yaml_path, encoding="utf-8", newline="") as fh:
        original = fh.read()

    updated, old_items = rewrite_values(original, args.var, values)
    print(f"原 values {old_items} 条 → 新 values {len(values)} 条")

    if updated == original:
        print("内容无变化，跳过写入。")
        return 0

    if args.dry_run:
        print("--dry-run：未写入。目标片段预览：")
        for line in updated.splitlines():
            if line.strip().startswith("- var_name"):
                show = updated.splitlines()[updated.splitlines().index(line):
                                             updated.splitlines().index(line) + 3 + len(values)]
                print("\n".join(show))
                break
        return 0

    tmp = yaml_path.with_suffix(yaml_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(updated)                 # newline="" 保留原换行风格
    os.replace(tmp, yaml_path)            # 原子替换
    print(f"已写回：{yaml_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
