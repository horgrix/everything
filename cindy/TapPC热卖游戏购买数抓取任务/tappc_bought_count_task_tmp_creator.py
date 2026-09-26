"""查询 crawler.db 中 taptap_pc_sell_ids_daily 表里 crawled_at
等于当前小时（本地时间）的数据，对 app_id 去重后按升序返回，并写回
config/tasks/user_trigger/TapPC热卖榜游戏售卖数采集任务.yaml
中 iterate 下 var_name == app_id 的 values 字段，保存到原文件。
"""
import sqlite3
import sys
from pathlib import Path

import yaml

# 脚本位于 cindy/TapPC在线人数抓取任务/ 下，向上两级即项目根目录，
# 这样无论从哪个目录运行本脚本，都能定位到 crawler.db 和 YAML 配置。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "crawler.db"
TABLE = "taptap_pc_sell_ids_daily"
YAML_PATH = PROJECT_ROOT / "config/tasks/user_trigger/TapPC热卖榜游戏售卖数采集任务.yaml"
VAR_NAME = "app_id"


def fetch_app_ids() -> list[int]:
    """查询表中 crawled_at 等于当前小时的数据，对 app_id 去重后按升序返回。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT app_id FROM {TABLE} "
            "WHERE app_id IS NOT NULL "
            "AND crawled_at = strftime('%Y-%m-%d', 'now', 'localtime') "
            "ORDER BY app_id"
        ).fetchall()
    finally:
        conn.close()
    return [int(row[0]) for row in rows]


def update_yaml_values(app_ids: list[int]) -> None:
    """把 app_id 列表写入 YAML 中 iterate 下 var_name == app_id 的 values。"""
    with open(YAML_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    target = None
    for item in config.get("iterate", []):
        if item.get("var_name") == VAR_NAME:
            target = item
            break
    if target is None:
        raise ValueError(f"未找到 iterate 中 var_name == '{VAR_NAME}' 的配置项")

    target["values"] = app_ids

    with open(YAML_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            config,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )


def main() -> None:
    # 确保 Windows 控制台以 UTF-8 输出，避免中文乱码
    sys.stdout.reconfigure(encoding="utf-8")

    app_ids = fetch_app_ids()
    if not app_ids:
        print(f"表 '{TABLE}' 中当前小时没有 app_id，未做修改")
        return

    update_yaml_values(app_ids)

    # 校验写回后的 YAML 仍可正常解析，且 values 已更新
    with open(YAML_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    written = next(
        (item.get("values", []) for item in config.get("iterate", [])
         if item.get("var_name") == VAR_NAME),
        None,
    )
    assert written == app_ids, "写回后的 values 与数据库中的 app_id 不一致"

    print(f"已将 {len(app_ids)} 个 app_id 写入 {YAML_PATH}")


if __name__ == "__main__":
    main()

