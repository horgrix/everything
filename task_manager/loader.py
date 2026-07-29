"""
任务加载模块：从 YAML 配置文件加载爬取任务，注册到数据库。

目录约定：
  config/tasks/system_trigger/  — system 类型定时任务
  config/tasks/user_trigger/    — user   类型手动任务
"""

import yaml
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class TaskLoader:
    """
    从指定目录加载所有 YAML 任务配置文件，
    解析为任务配置 dict，注册到数据库并创建对应的业务表。

    使用方式:
        loader = TaskLoader(config_dir="config/tasks", db=database)
        tasks = loader.load_all()
    """

    def __init__(self, config_dir: str, db):
        self.config_dir = Path(config_dir)
        self.db = db
        self._subdirs = ["system_trigger", "user_trigger"]

    def load_all(self) -> list[dict]:
        """扫描 system_trigger/ 和 user_trigger/ 子目录，解析并注册任务。"""
        if not self.config_dir.exists():
            logger.warning("配置目录不存在: %s", self.config_dir)
            return []

        tasks = []
        for sub in self._subdirs:
            sub_path = self.config_dir / sub
            if not sub_path.exists():
                continue
            trigger_type = "system" if sub == "system_trigger" else "user"
            for yaml_file in sorted(sub_path.glob("*.yaml")):
                tasks.extend(self._load_file(yaml_file, trigger_type))

        logger.info("共加载 %d 个任务", len(tasks))
        return tasks

    def _load_file(self, filepath: Path, trigger_type: str) -> list[dict]:
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
                processed = self._register_task(task_config, trigger_type, filepath)
                if processed:
                    results.append(processed)
            except Exception as e:
                name = task_config.get("name", "unknown")
                logger.error("注册任务 '%s' 失败: %s", name, e)

        return results

    def _register_task(self, config: dict, trigger_type: str = "system",
                       filepath: Path = None) -> Optional[dict]:
        """注册单个任务：验证 → 建表 → UPSERT。"""
        name = config.get("name")
        if not name:
            logger.error("任务缺少 name 字段")
            return None

        outputs_config = config.get("outputs", [])
        if not outputs_config:
            logger.error("任务 '%s' 缺少 outputs 字段", name)
            return None

        # 取第一个 output 的 table 作为主表记录
        first_table = outputs_config[0].get("target_table", "unknown")

        # trigger_type 必填且必须与目录一致
        trigger_type_in_config = config.get("trigger_type")
        if not trigger_type_in_config:
            logger.error("任务 '%s' 缺少 trigger_type 字段 (必填)", name)
            return None
        if trigger_type_in_config != trigger_type:
            logger.error("任务 '%s' trigger_type='%s' 与目录 '%s' 不匹配",
                         name, trigger_type_in_config, trigger_type)
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
        config_yaml = yaml.dump(config, allow_unicode=True, default_flow_style=False)

        task_id = self.db.upsert_task(
            name, config.get("type", "web"), first_table,
            schedule, config_yaml, trigger_type_in_config,
        )

        config["_task_id"] = task_id
        config["_source_file"] = str(filepath or self.config_dir)
        config["_trigger_type"] = trigger_type_in_config

        logger.info("任务注册成功: %s (id=%d, type=%s)", name, task_id, trigger_type_in_config)
        return config