"""查询 crawler.db 中 taptap_pc_hot_list_daily 表的 app_id 列表，
并写回 config/tasks/user_trigger/TaptapPC热门游戏统计数据采集任务.yaml
中 iterate 下 var_name == app_id 的 values 字段，保存到原文件。

更新完 YAML 后，清空 taptap_pc_hot_list_daily 表的所有数据。
"""
import sqlite3
import sys
from pathlib import Path

import yaml

DB_PATH = "crawler.db"
TABLE = "taptap_pc_hot_list_daily"
YAML_PATH = Path("config/tasks/user_trigger/TaptapPC热门游戏统计数据采集任务.yaml")
VAR_NAME = "app_id"


def fetch_app_ids() -> list[int]:
    """查询表中去重后的 app_id，按升序返回。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT app_id FROM {TABLE} "
            f"WHERE app_id IS NOT NULL ORDER BY app_id"
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


def clear_table() -> None:
    """删除 TABLE 表中所有数据，并重置自增 id。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        before = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        conn.execute(f"DELETE FROM {TABLE}")
        # 重置 AUTOINCREMENT 计数器（若该表存在自增主键）
        conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (TABLE,))
        conn.commit()
        after = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    finally:
        conn.close()
    print(f"表 '{TABLE}' 已清空：删除 {before} 行，剩余 {after} 行")


def main() -> None:
    # 确保 Windows 控制台以 UTF-8 输出，避免中文乱码
    sys.stdout.reconfigure(encoding="utf-8")

    app_ids = fetch_app_ids()
    if not app_ids:
        print(f"表 '{TABLE}' 中没有 app_id，未做修改")
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

    # 更新完 YAML 后，清空源表数据
    clear_table()


if __name__ == "__main__":
    main()

