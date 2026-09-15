"""清空 crawler.db 中 taptap_hot_list_game_hourly 表的数据。

执行后表内所有行被删除，并重置自增 id（下次插入从 1 开始）。
"""
import sqlite3
import sys

DB_PATH = "crawler.db"
TABLE = "taptap_hot_list_game_hourly"


def main() -> None:
    # 确保 Windows 控制台以 UTF-8 输出，避免中文乱码
    sys.stdout.reconfigure(encoding="utf-8")

    conn = sqlite3.connect(DB_PATH)
    try:
        before = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        conn.execute(f"DELETE FROM {TABLE}")
        # 重置 AUTOINCREMENT 计数器，使 id 从 1 重新开始
        conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (TABLE,))
        conn.commit()
        after = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    finally:
        conn.close()

    print(f"表 '{TABLE}' 已清空：删除 {before} 行，剩余 {after} 行，自增 id 已重置")


if __name__ == "__main__":
    main()

