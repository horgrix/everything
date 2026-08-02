"""
FastAPI dependencies: provides shared state to route handlers.

Replaces per-file _get_db/_get_config_dir helpers with a single source.
"""

import yaml
import logging
from fastapi import Request, HTTPException

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


VALID_TRIGGER_TYPES = frozenset({"system", "user"})


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

    - trigger_type is required and must be 'system' or 'user'
    - system tasks must have a schedule
    - user tasks must NOT have a schedule

    Returns the validated trigger_type.
    """
    trigger_type = config.get("trigger_type")
    if not trigger_type:
        raise HTTPException(status_code=400,
                           detail="trigger_type 字段必填（system 或 user）")
    if trigger_type not in VALID_TRIGGER_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"无效的 trigger_type: {trigger_type}"
        )

    if trigger_type == "system":
        if not config.get("schedule"):
            raise HTTPException(
                status_code=400,
                detail="system 类型任务必须包含 schedule 字段"
            )
    else:  # user
        if "schedule" in config:
            raise HTTPException(
                status_code=400,
                detail="user 类型任务不允许 schedule 字段"
            )

    return trigger_type
