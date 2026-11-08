#!/usr/bin/env python3
"""把本地 crawler.db 里指定时间的数据批量上传到远程服务器。

默认上传 taptap_game_info_hourly 的「当前小时」数据，可用参数指定时间/表：

    # 当前小时（默认）
    python config/tasks/utils/script/upload_data.py

    # 指定某个小时（可写多个，逗号分隔或用多次 --time）
    python config/tasks/utils/script/upload_data.py --time "2026-11-08 14"
    python config/tasks/utils/script/upload_data.py --time "2026-11-08 14,2026-11-08 15"

    # 指定一整天
    python config/tasks/utils/script/upload_data.py --date 2026-11-08

    # 先看会传多少行，不发请求
    python config/tasks/utils/script/upload_data.py --time "2026-11-08 14" --dry-run

要点：
  * 只上传业务列（排除本地自增 id），并且会和**服务端列**取交集 ——
    本地多出的列会被跳过并提示，避免服务端报 "Unknown column" 整批失败；
  * 按 --batch-size 分批 POST，单批失败只影响该批，最后汇总；
  * 上传接口是 UPSERT（按服务端唯一键），重复执行不会产生重复行；
  * 写接口（POST）需要 X-API-Key，读接口不需要。Key 的取值顺序：
    --api-key > 环境变量 CRAWLER_API_KEY > config/secrets.yaml 的 crawler_api_key
    （secrets.yaml 已在 .gitignore 里，不要把 Key 直接写进本文件）。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://horgrix.com/api/data"
DEFAULT_TABLES = ["taptap_game_info_hourly"]


def load_api_key(cli_key: str | None, root: Path) -> str | None:
    """按 --api-key > 环境变量 > config/secrets.yaml 的顺序取写接口所需的 Key。"""
    if cli_key:
        return cli_key.strip()
    env_key = os.environ.get("CRAWLER_API_KEY") or os.environ.get("API_KEY")
    if env_key:
        return env_key.strip()

    secrets_path = root / "config" / "secrets.yaml"
    if secrets_path.exists():
        try:
            import yaml
        except ImportError:
            return None
        data = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
        value = data.get("crawler_api_key") if isinstance(data, dict) else None
        return str(value).strip() if value else None
    return None


def find_project_root() -> Path:
    """向上找到含 crawler.db 的项目根目录（脚本位置变动也不会找错库）。"""
    for parent in Path(__file__).resolve().parents:
        if (parent / "crawler.db").exists():
            return parent
    # 兜底：config/tasks/utils/script → 项目根
    return Path(__file__).resolve().parents[4]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="上传本地库指定时间的数据到远程服务器")
    p.add_argument("--table", action="append", dest="tables",
                   help=f"要上传的表，可重复（默认 {DEFAULT_TABLES[0]}）")
    p.add_argument("--time", action="append", dest="times",
                   help='精确到小时的时间，如 "2026-11-08 14"；可逗号分隔多个')
    p.add_argument("--date", help="按天上传，如 2026-11-08（与该天所有小时）")
    p.add_argument("--db", default=None, help="本地 SQLite 库路径（默认项目根 crawler.db）")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL,
                   help=f"服务端 API 前缀（默认 {DEFAULT_BASE_URL}）")
    p.add_argument("--batch-size", type=int, default=500, help="每批行数（默认 500）")
    p.add_argument("--timeout", type=float, default=120.0, help="单次请求超时秒数（默认 120）")
    p.add_argument("--retries", type=int, default=1, help="单批失败重试次数（默认 1）")
    p.add_argument("--api-key", default=None,
                   help="写接口的 X-API-Key（默认取 CRAWLER_API_KEY 或用 config/secrets.yaml）")
    p.add_argument("--dry-run", action="store_true", help="只统计不上传")
    return p.parse_args(argv)


def build_where(args: argparse.Namespace) -> tuple[str, list]:
    """把 --time / --date 变成 SQL 条件；都不传就用当前小时。"""
    if args.date:
        return "substr(crawled_at, 1, 10) = ?", [args.date]

    raw_times: list[str] = []
    for chunk in args.times or []:
        raw_times.extend(x.strip() for x in chunk.split(",") if x.strip())
    if not raw_times:
        return "crawled_at = ?", [time.strftime("%Y-%m-%d %H", time.localtime())]

    marks = ", ".join("?" for _ in raw_times)
    return f"crawled_at IN ({marks})", raw_times


def local_rows(conn: sqlite3.Connection, table: str, where: str, params: list) -> list[dict]:
    conn.row_factory = sqlite3.Row
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    if not cols:
        raise SystemExit(f"本地没有表 {table}")
    select_cols = ", ".join(f'"{c}"' for c in cols if c != "id")
    rows = conn.execute(f"SELECT {select_cols} FROM {table} WHERE {where} ORDER BY crawled_at", params)
    return [dict(r) for r in rows]


def remote_columns(base_url: str, table: str, timeout: float) -> set[str]:
    """取服务端该表的列名；表不存在时返回空集。"""
    url = f"{base_url}/{table}/columns"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return set()
        raise SystemExit(f"取服务端列失败 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"连不上 {url}：{e.reason}")
    return {c["name"] for c in body.get("data") or []}


def post_batch(base_url: str, table: str, rows: list[dict], timeout: float,
               api_key: str | None = None) -> tuple[bool, str]:
    url = f"{base_url}/{table}/rows/batch"
    payload = json.dumps({"rows": rows}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            detail += "（写接口需要 X-API-Key：用 --api-key 或设置 CRAWLER_API_KEY）"
        return False, f"HTTP {e.code}: {detail}"
    except urllib.error.URLError as e:
        return False, f"连接失败：{e.reason}"

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        return False, f"HTTP {status} 响应非 JSON：{body[:200]}"
    ok = result.get("code") == 0
    return ok, result.get("data") or result.get("message") or ""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    root = find_project_root()
    db_path = Path(args.db) if args.db else root / "crawler.db"
    if not db_path.exists():
        raise SystemExit(f"库文件不存在：{db_path}")

    api_key = load_api_key(args.api_key, root)
    tables = args.tables or DEFAULT_TABLES
    where, params = build_where(args)
    print(f"库：{db_path}")
    print(f"条件：{where}  参数：{params}")
    print(f"目标：{args.base_url}  表：{', '.join(tables)}"
          + ("  [dry-run]" if args.dry_run else ""))
    if not args.dry_run:
        print("API Key：" + ("已配置" if api_key else "未配置（POST 会被服务端 401）"))

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    failed_total = 0
    try:
        for table in tables:
            rows = local_rows(conn, table, where, params)
            print(f"\n表 {table}：本地 {len(rows)} 行")
            if not rows:
                continue

            remote = remote_columns(args.base_url, table, args.timeout)
            if not remote:
                print(f"  服务端没有表 {table}，跳过")
                failed_total += 1
                continue

            local_cols = list(rows[0].keys())
            dropped = [c for c in local_cols if c not in remote]
            if dropped:
                print(f"  服务端缺少这些列，已跳过：{dropped}")
                rows = [{k: v for k, v in r.items() if k in remote} for r in rows]
            if not rows[0]:
                print("  剔除后没有可上传的列，跳过")
                failed_total += 1
                continue

            if args.dry_run:
                print(f"  可上传 {len(rows)} 行（{len(rows[0])} 列），dry-run 不发送")
                continue

            size = max(1, args.batch_size)
            for start in range(0, len(rows), size):
                batch = rows[start:start + size]
                for attempt in range(args.retries + 1):
                    ok, info = post_batch(args.base_url, table, batch, args.timeout, api_key)
                    if ok:
                        print(f"  批次 {start // size + 1}（{len(batch)} 行）成功：{info}")
                        break
                    if attempt < args.retries:
                        print(f"  批次 {start // size + 1} 失败，重试……（{info}）")
                        time.sleep(2)
                    else:
                        print(f"  批次 {start // size + 1} 失败：{info}")
                        failed_total += 1
    finally:
        conn.close()

    print(f"\n{'有失败项' if failed_total else '全部完成'}")
    return 1 if failed_total else 0


if __name__ == "__main__":
    sys.exit(main())
