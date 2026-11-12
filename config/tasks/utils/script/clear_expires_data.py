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

# 脚本位于 config/tasks/utils/script/ 下，向上一级即项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[4]
DB_PATH = PROJECT_ROOT / "crawler.db"

# (表名, crawled_at 的 strftime 格式, 保留时长修饰符)
RULES = [
    ("taptap_pc_search_discovery_ad_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("taptap_pc_search_hot_search_ad_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("taptap_app_android_search_discovery_ad_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("taptap_app_android_search_hot_search_ad_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("taptap_app_android_search_hot_spot_ad_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("taptap_app_ios_search_discovery_ad_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("taptap_app_ios_search_hot_search_ad_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("taptap_app_ios_search_hot_spot_ad_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("taptap_app_android_landing_ad_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("taptap_app_ios_landing_ad_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("taptap_pc_ad_discovery_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("taptap_pc_ad_top_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("dws_taptap_ad_keywords_app_v4_daily", "%Y-%m-%d", "-15 days"),
    ("dws_taptap_ad_landing_app_v4_daily", "%Y-%m-%d", "-15 days"),
    ("dws_taptap_ad_landing_pc_v4_daily", "%Y-%m-%d", "-15 days"),
    ("dws_taptap_ad_aggr_v6_daily", "%Y-%m-%d", "-15 days"),
    ("dws_taptap_ad_rank_v4_daily", "%Y-%m-%d", "-15 days"),
    ("dws_taptap_ad_overview_v5_daily", "%Y-%m-%d", "-15 days"),

    ("taptap_game_info_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("taptap_list_app_hot_ids_daily", "%Y-%m-%d", "-7 days"),
    ("taptap_list_app_reserve_ids_daily", "%Y-%m-%d", "-7 days"),
    ("taptap_list_pc_hot_ids_daily", "%Y-%m-%d", "-7 days"),
    ("taptap_list_pc_played_ids_daily", "%Y-%m-%d", "-7 days"),

    ("taptap_pc_online_players", "%Y-%m-%d %H", "-36 hours"),
    ("dws_taptap_pc_online_peak_players_details_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("dws_taptap_pc_online_peak_players_v2_hourly", "%Y-%m-%d %H", "-36 hours"),

    ("dws_taptap_download_v2_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("dws_taptap_download_summary_hourly", "%Y-%m-%d %H", "-36 hours"),

    ("dws_taptap_reserve_v2_hourly", "%Y-%m-%d %H", "-36 hours"),
    ("dws_taptap_reserve_summary_v2_hourly", "%Y-%m-%d %H", "-36 hours"),
]


def main() -> None:
    # 确保 Windows 控制台以 UTF-8 输出，避免中文乱码
    sys.stdout.reconfigure(encoding="utf-8")

    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table, fmt, modifier in RULES:
            if table not in existing:
                print(f"表 '{table}' 不存在，跳过")
                continue
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

