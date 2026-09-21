"""将 crawler.db 中三张 DWS 表的数据分别通过
https://horgrix.com/api/data/{table}/rows/batch 批量上传到远程服务器。

上传表及过滤范围：
- dws_taptap_download_hourly：仅上传当前小时数据
- dws_taptap_download_daily：仅上传当天数据
- dws_taptap_download_monthly：仅上传当月数据
- dws_taptap_game：仅上传当月数据

上传时排除本地自增 id 列，仅上传业务字段；rows 格式：[{列名: 值}, ...]。
"""
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 脚本位于 cindy/TapTap热门游戏DWS任务/ 下，向上两级即项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "crawler.db"

URL_TEMPLATE = "https://horgrix.com/api/data/{table}/rows/batch"
# (表名, crawled_at 的 strftime 格式) —— 仅上传当前小时/当天/当月数据
TABLES = [
    ("dws_taptap_download_hourly", "%Y-%m-%d %H"),
    ("dws_taptap_download_daily", "%Y-%m-%d"),
    ("dws_taptap_download_monthly", "%Y-%m"),
    ("dws_taptap_game", "%Y-%m"),
]


def fetch_rows(conn: sqlite3.Connection, table: str, fmt: str) -> tuple[list[dict], str]:
    """读取表中当前时间范围的数据（排除 id 列），返回 (rows, 当前时间字符串)。"""
    columns = [
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        if row["name"] != "id"
    ]
    select_clause = ", ".join(columns)
    current = conn.execute(
        f"SELECT strftime('{fmt}', 'now', 'localtime')"
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT {select_clause} FROM {table} WHERE crawled_at = ?",
        (current,),
    ).fetchall()
    return [dict(row) for row in rows], current


def upload(table: str, rows: list[dict]) -> bool:
    """上传单表数据，成功返回 True，失败返回 False。"""
    url = URL_TEMPLATE.format(table=table)
    payload = json.dumps({"rows": rows}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"表 '{table}' 上传失败 HTTP {e.code}: {body}")
        return False
    except urllib.error.URLError as e:
        print(f"表 '{table}' 上传失败：{e.reason}")
        return False

    print(f"表 '{table}' HTTP {status}: {body}")
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        result = {}
    return result.get("code") == 0


def main() -> int:
    # 确保 Windows 控制台以 UTF-8 输出，避免中文乱码
    sys.stdout.reconfigure(encoding="utf-8")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        data = {}
        for table, fmt in TABLES:
            rows, current = fetch_rows(conn, table, fmt)
            print(f"表 '{table}' 当前时间({current})数据：{len(rows)} 行")
            data[table] = rows
    finally:
        conn.close()

    failed = []
    for table, _ in TABLES:
        rows = data[table]
        if not rows:
            print(f"表 '{table}' 当前时间无数据可上传")
            continue
        if not upload(table, rows):
            failed.append(table)

    if failed:
        print(f"上传失败的表：{', '.join(failed)}")
        return 1

    print("全部上传完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())

