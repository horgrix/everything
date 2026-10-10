"""
任务加载模块：从 YAML 配置文件加载爬取任务，注册到数据库。

目录约定（目录名 = 分组 group）：
  config/tasks/<group>/*.yaml         — system 类型定时任务（组根）
  config/tasks/<group>/manual/*.yaml  — user   类型手动任务
  config/tasks/<group>/README.md      — 组元数据说明（跳过）
  config/tasks/<group>/script/        — 脚本目录（跳过）
"""

import yaml
import logging
from pathlib import Path
from typing import Optional

from .schema import TaskConfig

logger = logging.getLogger(__name__)


class TaskLoader:
    """
    扫描 config/tasks/ 下所有一级子目录作为分组（group），
    每个分组下加载组根 *.yaml（system）与 manual/*.yaml（user），
    解析为任务配置 dict，注册到数据库并创建对应的业务表。

    使用方式:
        loader = TaskLoader(config_dir="config/tasks", db=database)
        tasks = loader.load_all()
    """

    def __init__(self, config_dir: str, db):
        self.config_dir = Path(config_dir)
        self.db = db

    def load_all(self) -> list[dict]:
        """扫描所有分组目录，解析并注册任务。"""
        if not self.config_dir.exists():
            logger.warning("配置目录不存在: %s", self.config_dir)
            return []

        tasks = []
        for group_dir in sorted(self.config_dir.iterdir()):
            # 只扫描一级子目录（跳过非目录项，如 _example_all_features.yaml）
            if not group_dir.is_dir():
                continue
            group = group_dir.name
            tasks.extend(self._load_group(group_dir, group))

        logger.info("共加载 %d 个任务", len(tasks))
        return tasks

    def _load_group(self, group_dir: Path, group: str) -> list[dict]:
        """加载单个分组的组根 system 任务与 manual/ user 任务。"""
        tasks = []

        # 组根 *.yaml（system）
        for yaml_file in sorted(group_dir.glob("*.yaml")):
            tasks.extend(self._load_file(yaml_file, "system", group))

        # manual/*.yaml（user）
        manual_dir = group_dir / "manual"
        if manual_dir.is_dir():
            for yaml_file in sorted(manual_dir.glob("*.yaml")):
                tasks.extend(self._load_file(yaml_file, "user", group))

        return tasks

    def _load_file(self, filepath: Path, trigger_type: str, group: str) -> list[dict]:
        """加载单个 YAML 文件（可能包含多个任务）"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.error("YAML 解析失败: %s - %s", filepath, e)
            return []
        except Exception as e:
            logger.error("读取文件失败: %s - %s", filepath, e)
            return []

        if data is None:
            logger.warning("空配置文件: %s", filepath)
            return []

        task_list = data if isinstance(data, list) else [data]
        results = []
        for task_config in task_list:
            try:
                processed = self._register_task(
                    task_config, trigger_type, filepath, group
                )
                if processed:
                    results.append(processed)
            except Exception as e:
                name = task_config.get("name", "unknown")
                logger.error("注册任务 '%s' 失败: %s", name, e)

        return results

    def _register_task(self, config: dict, trigger_type: str = "system",
                       filepath: Path = None, group: str = "") -> Optional[dict]:
        """注册单个任务：验证 → 建表 → UPSERT。"""
        name = config.get("name")
        if not name:
            logger.error("任务缺少 name 字段")
            return None

        outputs_config = config.get("outputs", [])
        if not outputs_config:
            logger.error("任务 '%s' 缺少 outputs 字段", name)
            return None

        # 每个 output 至少要有 target_table 或 target_api 之一
        for output_config in outputs_config:
            if not output_config.get("target_table") and not output_config.get("target_api"):
                logger.error(
                    "任务 '%s' 的 output 缺少 target_table 或 target_api", name
                )
                return None

        # 取第一个 output 的 table 作为主表记录
        first_table = outputs_config[0].get("target_table", "unknown")

        # trigger_type 必填且必须与目录一致
        trigger_type_in_config = config.get("trigger_type")
        if not trigger_type_in_config:
            logger.error("任务 '%s' 缺少 trigger_type 字段 (必填)", name)
            return None
        if trigger_type_in_config != trigger_type:
            logger.error(
                "任务 '%s' trigger_type='%s' 与目录位置（%s）不匹配",
                name, trigger_type_in_config, trigger_type,
            )
            return None

        # schedule 校验
        schedule = config.get("schedule")
        if trigger_type_in_config == "system":
            if not schedule:
                logger.error("任务 '%s' (system) 缺少 schedule 字段", name)
                return None
        else:  # user
            if "schedule" in config:
                logger.error("任务 '%s' (user) 不允许 schedule 字段", name)
                return None
            schedule = ""  # DB NOT NULL 约束用

        # 创建业务表
        for output_config in outputs_config:
            schema = output_config.get("table_schema", {})
            columns = schema.get("columns", [])
            indexes = schema.get("indexes", [])
            table = output_config.get("target_table", "")
            if columns and table:
                self.db.ensure_business_table(table, columns, indexes)
                logger.info("业务表 '%s' 已就绪", table)

        # 序列化完整配置（含 outputs）
        # 注意：group 由目录名推导，不写入 config_yaml，保持与磁盘 YAML 一致
        config_yaml = yaml.dump(config, allow_unicode=True, default_flow_style=False)

        task_id = self.db.upsert_task(
            name, config.get("type", "web"), first_table,
            schedule, config_yaml, trigger_type_in_config, group,
        )

        config["_task_id"] = task_id
        config["_source_file"] = str(filepath or self.config_dir)
        config["_trigger_type"] = trigger_type_in_config
        config["_group"] = group

        logger.info(
            "任务注册成功: %s (id=%d, type=%s, group=%s)",
            name, task_id, trigger_type_in_config, group,
        )
        return TaskConfig.from_dict(config)
