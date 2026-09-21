"""清理 TapTap 热门游戏 DWS 三张表的历史数据。

清理规则：
- dws_taptap_download_hourly：删除 crawled_at 早于最近 72 小时的数据（格式 YYYY-MM-DD HH）
- dws_taptap_download_daily：删除 crawled_at 早于最近 30 天的数据（格式 YYYY-MM-DD）
- dws_taptap_download_monthly：删除 crawled_at 早于最近 24 个月的数据（格式 YYYY-MM）

crawled_at 都是可字典序比较的字符串，直接用 SQLite strftime 计算阈值后比较。
"""
import sqlite3
import sys
from pathlib import Path

# 脚本位于 cindy/TapTap热门游戏DWS任务/ 下，向上两级即项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "crawler.db"

# (表名, crawled_at 的 strftime 格式, 保留时长修饰符)
RULES = [
    ("dws_taptap_download_hourly", "%Y-%m-%d %H", "-72 hours"),
    ("dws_taptap_download_daily", "%Y-%m-%d", "-30 days"),
    ("dws_taptap_download_monthly", "%Y-%m", "-24 months"),
]


def main() -> None:
    # 确保 Windows 控制台以 UTF-8 输出，避免中文乱码
    sys.stdout.reconfigure(encoding="utf-8")

    conn = sqlite3.connect(DB_PATH)
    try:
        for table, fmt, modifier in RULES:
            before = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            threshold = conn.execute(
                f"SELECT strftime('{fmt}', 'now', 'localtime', ?)",
                (modifier,),
            ).fetchone()[0]
            conn.execute(
                f"DELETE FROM {table} WHERE crawled_at < ?",
                (threshold,),
            )
            conn.commit()
            after = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            print(
                f"表 '{table}' 已清理：删除 {before - after} 行"
                f"（crawled_at < {threshold}），剩余 {after} 行"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()

