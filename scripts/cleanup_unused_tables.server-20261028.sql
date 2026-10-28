-- 线上库 crawler.db 无用表清理脚本（horgrix.com:/home/ubuntu/projects/everything/crawler.db）
-- 生成时间：2026-10-28 17:20（基于 2026-10-28 17:15 的备份 crawler-20261028-171530.db 审计）
--
-- 判定依据：
--   写入方 = config/tasks 任务的 target_table，或 api 日志里 POST /api/data/{table}/rows/batch 的外部采集端
--   消费方 = config/tasks 任务的 SQL、everything/api、everything-ui 里走 SQLite 的页面
--
-- 使用方式（务必先备份，再在备份上试跑）：
--   sqlite3 crawler.db ".backup /home/ubuntu/backups/crawler-$(date +%Y%m%d-%H%M%S).db"
--   sqlite3 /home/ubuntu/backups/crawler-<新备份>.db < 本文件     # 先在备份上验证
--   sqlite3 crawler.db < 本文件                                  # 确认无误再打线上

PRAGMA foreign_keys = OFF;
BEGIN;

-- ===== 第 1 组：完全无人使用（无写入 / 无读取 / 无代码引用），25 张 =====
-- 广告聚合 v1，被 v5 取代｜3839 行｜最新 2026-10-25｜296KB
DROP TABLE IF EXISTS "dws_taptap_ad_aggr_daily";
-- 广告聚合 v2，空表｜0 行｜最新 -｜4KB
DROP TABLE IF EXISTS "dws_taptap_ad_aggr_v2_daily";
-- 广告聚合 v4，已被 v5 取代｜1164 行｜最新 2026-10-28｜192KB
DROP TABLE IF EXISTS "dws_taptap_ad_aggr_v4_daily";
-- 广告汇总 v4，已被 v5 取代｜19 行｜最新 2026-10-28｜4KB
DROP TABLE IF EXISTS "dws_taptap_ad_overview_v4_daily";
-- 关键词广告计算 v2，已被 v4 取代｜2898 行｜最新 2026-10-28｜276KB
DROP TABLE IF EXISTS "dws_taptap_app_keywords_ad_v2_daily";
-- 关键词广告汇总，任务已下线｜24 行｜最新 2026-10-28｜4KB
DROP TABLE IF EXISTS "dws_taptap_app_keywords_ad_overview";
-- 关键词广告排名旧表｜330 行｜最新 2026-42｜24KB
DROP TABLE IF EXISTS "dws_taptap_app_keywords_ad_rank";
-- App首页广告汇总，任务已下线｜24 行｜最新 2026-10-28｜4KB
DROP TABLE IF EXISTS "dws_taptap_app_landing_ad_overview";
-- App首页广告每时汇总，任务已下线｜520 行｜最新 2026-10-28 15｜24KB
DROP TABLE IF EXISTS "dws_taptap_app_landing_ad_overview_hourly";
-- App首页广告每月，任务已下线｜1074 行｜最新 2026-10｜80KB
DROP TABLE IF EXISTS "dws_taptap_app_landing_ad_monthly";
-- App首页广告排名旧表｜1441 行｜最新 2026-42｜56KB
DROP TABLE IF EXISTS "dws_taptap_app_landing_ad_rank";
-- 畅销榜旧表，采集端已停｜359 行｜最新 2026-09-27｜16KB
DROP TABLE IF EXISTS "dws_taptap_sell_daily";
-- 畅销榜旧表，空表｜0 行｜最新 -｜4KB
DROP TABLE IF EXISTS "dws_taptap_sell_hourly";
-- 畅销榜旧表，采集端已停｜182 行｜最新 2026-09｜12KB
DROP TABLE IF EXISTS "dws_taptap_sell_monthly";
-- 峰值玩家旧表｜199 行｜最新 2026-10-10 10｜12KB
DROP TABLE IF EXISTS "dws_taptap_peak_players_hourly";
-- 临时表｜216 行｜最新 2026-10-10 10｜12KB
DROP TABLE IF EXISTS "dws_tmp_taptap_peak_players_hourly";
-- 无任务、无数据｜0 行｜最新 -｜4KB
DROP TABLE IF EXISTS "dws_taptap_pc_source_online_players";
-- 无任务、无数据｜0 行｜最新 -｜4KB
DROP TABLE IF EXISTS "dws_taptap_pc_source_games_online_players";
-- 火炬之光专项旧表｜273 行｜最新 2026-10-15｜20KB
DROP TABLE IF EXISTS "dws_games_torchlight_download_daily";
-- 火炬之光专项旧表｜93 行｜最新 2026-10-17｜12KB
DROP TABLE IF EXISTS "dws_games_torchlight_ios_download_daily";
-- 火炬之光专项旧表｜4 行｜最新 2026-10-18｜4KB
DROP TABLE IF EXISTS "dws_games_torchlight_tap_download_daily";
-- 火炬之光赛季旧表｜540 行｜最新 2026-10-12 01:35:11｜28KB
DROP TABLE IF EXISTS "torchlight_season_steam_peak_players";
-- 无任务、无数据｜0 行｜最新 -｜4KB
DROP TABLE IF EXISTS "taptap_pc_ad_load";
-- 无任务、无数据｜0 行｜最新 -｜4KB
DROP TABLE IF EXISTS "taptap_pc_sell_ids_daily";
-- docs 教程示例表｜0 行｜最新 -｜4KB
DROP TABLE IF EXISTS "hello_api";

-- ===== 第 2 组：仅 MySQL(v09) 同名表被页面查询，SQLite 副本无写入方，3 张 =====
-- 仅被 OneSearch 页面以同名表从 MySQL(v09) 查询；SQLite 侧无任何写入方，属 v09 拆分前的旧副本｜2997 行｜最新 2026-10-25｜236KB
DROP TABLE IF EXISTS "dws_taptap_app_keywords_ad_daily";
-- 仅被 OneSearch 页面以同名表从 MySQL(v09) 查询；SQLite 侧无任何写入方，属 v09 拆分前的旧副本｜4438 行｜最新 2026-10-28｜372KB
DROP TABLE IF EXISTS "dws_taptap_app_landing_ad_daily";
-- 仅被 OneSearch 页面以同名表从 MySQL(v09) 查询；SQLite 侧无任何写入方，属 v09 拆分前的旧副本｜806 行｜最新 2026-10-28｜76KB
DROP TABLE IF EXISTS "dws_taptap_pc_ad_landing_daily";

COMMIT;

-- =====================================================================
-- 第 3 组：没有消费方，但写入来源还在 —— 删表前必须先停掉写入方。
-- 确认停掉后，把对应那一小段取消注释再执行。
-- =====================================================================

-- 3a. 任务仍在写（需先把这些任务停用/删除）
-- 写入任务：DWS_火炬之光赛季日下载数计算任务｜210 行｜最新 2026-10-27｜16KB
-- DROP TABLE IF EXISTS "dws_games_torchlight_season_download";
-- 写入任务：Steam游戏每月峰值玩家采集任务｜98 行｜最新 2026-10-02 08:35:00｜12KB
-- DROP TABLE IF EXISTS "steam_game_peak_players_monthly";
-- 写入任务：Steam游戏玩家每日评价采集任务｜139 行｜最新 2026-10-28 08:00:09｜12KB
-- DROP TABLE IF EXISTS "steam_player_review_recent";
-- 写入任务：Steam游戏玩家每日评价采集任务｜132 行｜最新 2026-10-25 08:00:10｜12KB
-- DROP TABLE IF EXISTS "steam_player_review_rollup";
-- 写入任务：交易所股票日线信息采集任务｜1689 行｜最新 2026-10-28 07:00:01｜160KB
-- DROP TABLE IF EXISTS "stocks_daily_kline";
-- 写入任务：TapPC热门游戏ID集合爬取任务｜0 行｜最新 -｜4KB
-- DROP TABLE IF EXISTS "taptap_pc_hot_list_ids_hourly";
-- 写入任务：火炬之光S11赛季Steam游戏每天峰值玩家离线任务｜182 行｜最新 2026-07-30 10:41:18｜12KB
-- DROP TABLE IF EXISTS "torchlight_ss11_steam_game_peak_players_daily";

-- 3b. 外部采集端曾 POST 写入，现已停止且无消费方（数据已停更 9 天以上）
-- 外部采集端 POST（仅 1 次，已停）｜1086 行｜最新 2026-10｜68KB
-- DROP TABLE IF EXISTS "dws_taptap_game_basic_info_monthly";
-- 外部采集端 POST（9 天前已停）｜2907 行｜最新 2026-10-19 21｜220KB
-- DROP TABLE IF EXISTS "taptap_ad_loading_hourly";
-- 外部采集端 POST（19 天前已停）｜4763 行｜最新 2026-10-10｜252KB
-- DROP TABLE IF EXISTS "taptap_pc_played_ids_daily";
-- 外部采集端 POST（9 天前已停）｜1800 行｜最新 2026-10-19 21｜140KB
-- DROP TABLE IF EXISTS "taptap_pc_top_ad_hourly";
-- 外部采集端 POST（空表）｜0 行｜最新 -｜4KB
-- DROP TABLE IF EXISTS "taptap_app_sell_list_hourly";
-- 外部采集端 POST（空表）｜0 行｜最新 -｜4KB
-- DROP TABLE IF EXISTS "taptap_pc_sell_list_hourly";

-- 回收空间（可选，会锁库并重写整个文件，避开整点任务窗口）
-- VACUUM;
