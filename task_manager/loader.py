"""
任务加载模块：从 YAML 配置文件加载爬取任务，注册到数据库。
"""

import os
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

    def load_all(self) -> list[dict]:
        """
        扫描 config_dir 下所有 .yaml/.yml 文件，
        解析并注册任务，返回任务配置列表。
        """
        if not self.config_dir.exists():
            logger.warning("配置目录不存在: %s", self.config_dir)
            return []

        tasks = []
        for yaml_file in sorted(self.config_dir.glob("*.yaml")):
            tasks.extend(self._load_file(yaml_file))
        for yaml_file in sorted(self.config_dir.glob("*.yml")):
            tasks.extend(self._load_file(yaml_file))

        logger.info("共加载 %d 个任务", len(tasks))
        return tasks

    def _load_file(self, filepath: Path) -> list[dict]:
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

        # 支持单任务和多任务（列表）
        if isinstance(data, list):
            task_list = data
        else:
            task_list = [data]

        results = []
        for task_config in task_list:
            try:
                processed = self._register_task(task_config)
                if processed:
                    results.append(processed)
            except Exception as e:
                name = task_config.get("name", "unknown")
                logger.error("注册任务 '%s' 失败: %s", name, e)

        return results

    def _register_task(self, config: dict) -> Optional[dict]:
        """
        注册单个任务：
        1. 验证必要字段
        2. 创建/同步业务表结构
        3. UPSERT 到 crawl_tasks 表
        """
        # 验证
        name = config.get("name")
        if not name:
            logger.error("任务缺少 name 字段")
            return None

        target_table = config.get("target_table")
        outputs_config = config.get("outputs", [])
        if not target_table and not outputs_config:
            logger.error("任务 '%s' 缺少 target_table 或 outputs 字段", name)
            return None
        if not target_table and outputs_config:
            target_table = outputs_config[0]["target_table"]

        schedule = config.get("schedule")
        if not schedule:
            logger.error("任务 '%s' 缺少 schedule 字段", name)
            return None

        task_type = config.get("type", "web")

        # 创建业务表（含索引）
        table_schema = config.get("table_schema", {})
        columns = table_schema.get("columns", [])
        indexes = table_schema.get("indexes", [])

        if columns and target_table:
            self.db.ensure_business_table(target_table, columns, indexes)
            logger.info("业务表 '%s' 已就绪", target_table)

        # outputs 中的表也需要创建
        for output_config in outputs_config:
            out_table = output_config.get("target_table", "")
            out_schema = output_config.get("table_schema", {})
            out_columns = out_schema.get("columns", [])
            out_indexes = out_schema.get("indexes", [])
            if out_columns and out_table:
                self.db.ensure_business_table(out_table, out_columns, out_indexes)
                logger.info("输出业务表 '%s' 已就绪", out_table)

        # 序列化完整配置
        config_yaml = yaml.dump(config, allow_unicode=True, default_flow_style=False)

        # UPSERT 任务记录
        task_id = self.db.upsert_task(name, task_type, target_table, schedule, config_yaml)

        # 将 task_id 注入回 config，供 engine 使用
        config["_task_id"] = task_id
        config["_source_file"] = str(self.config_dir)

        logger.info("任务注册成功: %s (id=%d, table=%s, schedule=%s)",
                     name, task_id, target_table, schedule)
        return config