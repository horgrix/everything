-- crawler.db 历史表清理脚本
-- 生成时间：2026-10-28 17:03:49
-- 判定口径：config/tasks 无人写入、无人读取；代码（everything/api、everything-ui/src、
--           tests、storage、crawler、task_manager、scheduler、../everything-api）无引用。
-- 生成前先备份：sqlite3 crawler.db ".backup backups/crawler-<时间戳>.db"
-- 执行：sqlite3 crawler.db < scripts/cleanup_unused_tables.sql
--
-- 共 50 张表

PRAGMA foreign_keys = OFF;
BEGIN;

-- ===== 广告 DWS 旧版本链（17 张）=====
-- 关键词广告每日计算已被 v4 替代｜清理前行数 2583｜最新数据 2026-10-28
DROP TABLE IF EXISTS "dws_taptap_app_keywords_ad_v2_daily";
-- 广告每日聚合 v2｜清理前行数 933｜最新数据 2026-10-25
DROP TABLE IF EXISTS "dws_taptap_ad_aggr_v2_daily";
-- 广告每日聚合 v3｜清理前行数 602｜最新数据 2026-10-25
DROP TABLE IF EXISTS "dws_taptap_ad_aggr_v3_daily";
-- 广告每日聚合 v4，现为 v5｜清理前行数 605｜最新数据 2026-10-25
DROP TABLE IF EXISTS "dws_taptap_ad_aggr_v4_daily";
-- 广告每日聚合 v1｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "dws_taptap_ad_aggr_daily";
-- 广告每日汇总 v4，现为 v5｜清理前行数 16｜最新数据 2026-10-25
DROP TABLE IF EXISTS "dws_taptap_ad_overview_v4_daily";
-- PC首页广告 v2｜清理前行数 825｜最新数据 2026-10-28
DROP TABLE IF EXISTS "dws_taptap_ad_landing_pc_v2_daily";
-- PC首页广告 v3，现为 v4｜清理前行数 19｜最新数据 2026-10-28
DROP TABLE IF EXISTS "dws_taptap_ad_landing_pc_v3_daily";
-- PC首页广告旧命名｜清理前行数 72｜最新数据 2026-10-28
DROP TABLE IF EXISTS "dws_taptap_ad_pc_landing_v2_daily";
-- 首页广告旧命名｜清理前行数 372｜最新数据 2026-10-07
DROP TABLE IF EXISTS "dws_taptap_landing_ad_daily";
-- 关键词广告汇总，任务已下线｜清理前行数 4｜最新数据 2026-10-08
DROP TABLE IF EXISTS "dws_taptap_app_keywords_ad_overview";
-- App首页广告汇总，任务已下线｜清理前行数 4｜最新数据 2026-10-11
DROP TABLE IF EXISTS "dws_taptap_app_landing_ad_overview";
-- App首页广告每时汇总，任务已下线｜清理前行数 44｜最新数据 2026-10-11 21
DROP TABLE IF EXISTS "dws_taptap_app_landing_ad_overview_hourly";
-- App首页广告每月，任务已下线｜清理前行数 316｜最新数据 2026-10
DROP TABLE IF EXISTS "dws_taptap_app_landing_ad_monthly";
-- 广告排名旧表，现为 dws_taptap_ad_rank_v4_daily｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "dws_taptap_ad_rank";
-- 关键词广告排名旧表｜清理前行数 45｜最新数据 2026-40
DROP TABLE IF EXISTS "dws_taptap_app_keywords_ad_rank";
-- App首页广告排名旧表｜清理前行数 146｜最新数据 2026-40
DROP TABLE IF EXISTS "dws_taptap_app_landing_ad_rank";

-- ===== 旧采集原始表（14 张）=====
-- 采集任务已删；且 crawled_at 存的是未渲染的 {now:...} 模板｜清理前行数 3419｜最新数据 {now:%Y-%m-%d %H}
DROP TABLE IF EXISTS "taptap_ad_loading_hourly";
-- 手滑建的空表｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "taptap_ad_loading_hourlysadasdsadadsadsadasdsa";
-- 采集任务已删｜清理前行数 20｜最新数据 2026-10-05 10
DROP TABLE IF EXISTS "taptap_app_ios_ad_loading_hourly";
-- 采集任务已删｜清理前行数 22｜最新数据 2026-09-25
DROP TABLE IF EXISTS "taptap_ad_hourly";
-- 采集任务已删（现用 taptap_pc_ad_top_hourly）｜清理前行数 1968｜最新数据 2026-10-19 21
DROP TABLE IF EXISTS "taptap_pc_top_ad_hourly";
-- 采集任务已删｜清理前行数 4767｜最新数据 2026-10-10
DROP TABLE IF EXISTS "taptap_pc_played_ids_daily";
-- 采集任务已删｜清理前行数 150｜最新数据 2026-09-25
DROP TABLE IF EXISTS "taptap_pc_played_ids_hourly";
-- 采集任务已删｜清理前行数 96｜最新数据 2026-09-27
DROP TABLE IF EXISTS "taptap_pc_sell_ids_daily";
-- 采集任务已删｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "taptap_app_sell_list_hourly";
-- 采集任务已删｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "taptap_pc_sell_list_hourly";
-- 采集任务已删｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "taptap_pc_ad_load";
-- 采集任务已删｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "taptap_pc_hot_list_daily";
-- 采集任务已删｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "taptap_rep_monthly";
-- 拼写错误的空表，无任务｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "ios_games_downlod_daily";

-- ===== 其他 DWS / 临时表（10 张）=====
-- 在线人数旧日表｜清理前行数 455｜最新数据 2026-10-09
DROP TABLE IF EXISTS "dws_taptap_pc_peak_players_daily";
-- 游戏基础信息月表，已被 dws_taptap_game 取代｜清理前行数 255｜最新数据 2026-10
DROP TABLE IF EXISTS "dws_taptap_game_basic_info_monthly";
-- 峰值玩家旧表｜清理前行数 34｜最新数据 2026-09-25 08
DROP TABLE IF EXISTS "dws_taptap_peak_players_hourly";
-- 临时表｜清理前行数 9｜最新数据 2026-09-25 08
DROP TABLE IF EXISTS "dws_tmp_taptap_peak_players_hourly";
-- 畅销榜旧表｜清理前行数 24｜最新数据 2026-09-26
DROP TABLE IF EXISTS "dws_taptap_sell_daily";
-- 畅销榜旧表｜清理前行数 24｜最新数据 2026-09
DROP TABLE IF EXISTS "dws_taptap_sell_monthly";
-- 畅销榜旧表｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "dws_taptap_sell_hourly";
-- 无任务、无数据｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "dws_taptap_pc_peak_online_players";
-- 无任务、无数据｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "dws_taptap_pc_source_online_players";
-- 无任务、无数据｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "dws_taptap_pc_source_games_online_players";

-- ===== 火炬之光 / 心动小镇系列（6 张）=====
-- 专项项目旧表｜清理前行数 273｜最新数据 2026-10-15
DROP TABLE IF EXISTS "dws_games_torchlight_download_daily";
-- 专项项目旧表｜清理前行数 273｜最新数据 2026-10-15
DROP TABLE IF EXISTS "dws_games_torchlight_ios_download_daily";
-- 专项项目旧表｜清理前行数 1｜最新数据 2026-10-15
DROP TABLE IF EXISTS "dws_games_torchlight_tap_download_daily";
-- 专项项目旧表｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "dws_games_torchlight_online_players";
-- 无任务、无数据｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "torchlight_season_steam_peak_players";
-- 表名误含零宽字符 U+200C，从未被写入｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "dws_games_heartopia‌_online_players_hourly";

-- ===== 杂项（3 张）=====
-- docs 教程示例表，无任务（仅文档提及）｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "hello_api";
-- 无任务、无数据｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "dws_taptap_games";
-- 临时表｜清理前行数 0｜最新数据 -
DROP TABLE IF EXISTS "dws_taptap_download_daily_temp";

COMMIT;

-- 回收磁盘空间（可选，单独事务，执行期间会重写整个库文件）
-- VACUUM;
