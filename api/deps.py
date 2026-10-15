"""
FastAPI dependencies: provides shared state to route handlers.

Replaces per-file _get_db/_get_config_dir helpers with a single source.
"""

import yaml
import logging
from fastapi import Request, HTTPException

from task_manager.validation import (
    TriggerTypeError,
    validate_trigger_type as _validate_trigger_type,
)

logger = logging.getLogger(__name__)


def get_db(request: Request):
    """Return the Database instance from app state."""
    return request.app.state.db


def get_config_dir(request: Request) -> str:
    """Return the task config directory from app state."""
    return request.app.state.config_dir


def get_scheduler(request: Request):
    """Return the scheduler instance (may be None in API-only mode)."""
    return getattr(request.app.state, "scheduler", None)


# ── Task validation (shared between create and update) ──────────


DEFAULT_GROUP = "default"


def validate_group(group: str) -> str:
    """校验并规范化 group 名：非空、不含路径分隔符。

    group 由目录名推导，创建/更新接口可显式指定；
    空值回退到 DEFAULT_GROUP。
    """
    group = (group or "").strip()
    if not group:
        return DEFAULT_GROUP
    if "/" in group or "\\" in group or group in (".", ".."):
        raise HTTPException(status_code=400, detail=f"无效的 group 名: {group}")
    return group


def parse_task_yaml(yaml_content: str, task_name: str) -> dict:
    """
    Parse and validate the raw YAML string for create/update.

    Returns the parsed dict, ready for registration.
    Raises HTTPException on any validation failure.
    """
    if not yaml_content.strip():
        raise HTTPException(status_code=400, detail="YAML 配置不能为空")

    try:
        config = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"YAML 解析失败: {e}")

    if not isinstance(config, dict):
        raise HTTPException(status_code=400,
                           detail="YAML 顶层必须是一个字典（任务配置）")

    config["name"] = task_name
    return config


def validate_trigger_type(config: dict) -> str:
    """
    Validate trigger_type and schedule cross-constraints.

    规则唯一实现在 :func:`task_manager.validation.validate_trigger_type`；
    本函数仅把 :class:`TriggerTypeError` 转成 HTTPException(400)。

    Returns the validated trigger_type.
    """
    try:
        return _validate_trigger_type(config)
    except TriggerTypeError as e:
        raise HTTPException(status_code=400, detail=str(e))
