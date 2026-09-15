"""读取 crawler.db 的 taptap_hot_list_game_hourly 表，以 JSON 形式输出到控制台。

输出格式：[{列名: 值}, ...]
"""
import json
import sqlite3
import sys

DB_PATH = "crawler.db"
TABLE = "taptap_hot_list_game_hourly"


def main() -> None:
    # 确保 Windows 控制台以 UTF-8 输出，避免中文乱码
    sys.stdout.reconfigure(encoding="utf-8")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # 排除 id 列，仅导出业务字段
        columns = [
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({TABLE})").fetchall()
            if row["name"] != "id"
        ]
        select_clause = ", ".join(columns)
        rows = conn.execute(f"SELECT {select_clause} FROM {TABLE}").fetchall()
    finally:
        conn.close()

    data = [dict(row) for row in rows]
    print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
