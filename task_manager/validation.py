"""
trigger_type / schedule 校验的唯一实现。

规则（loader 与 API 共用同一份）：
  - ``trigger_type`` 必填，且必须是 ``system`` / ``user``
  - ``system`` 任务必须有 ``schedule``
  - ``user`` 任务不允许有 ``schedule``
  - 可选：要求 ``trigger_type`` 与所在目录推导出的类型一致

调用方负责把 :class:`TriggerTypeError` 转成各自的错误形式：
loader 记日志并跳过该任务，API 返回 HTTP 400。
"""

VALID_TRIGGER_TYPES = frozenset({"system", "user"})


class TriggerTypeError(ValueError):
    """trigger_type / schedule 校验失败。"""


def validate_trigger_type(
    config: dict, expected_trigger_type: str | None = None
) -> str:
    """校验配置中的 trigger_type 与 schedule 约束，返回校验后的 trigger_type。

    参数：
        config: 任务配置 dict
        expected_trigger_type: 目录位置要求的类型（loader 传入）；
            ``None`` 表示不校验目录一致性（API 创建/更新场景）

    异常：
        :class:`TriggerTypeError`：任一规则不满足
    """
    trigger_type = config.get("trigger_type")
    if not trigger_type:
        raise TriggerTypeError("trigger_type 字段必填（system 或 user）")
    if trigger_type not in VALID_TRIGGER_TYPES:
        raise TriggerTypeError(f"无效的 trigger_type: {trigger_type}")
    if expected_trigger_type is not None and trigger_type != expected_trigger_type:
        raise TriggerTypeError(
            f"trigger_type='{trigger_type}' 与目录位置（{expected_trigger_type}）不匹配"
        )

    if trigger_type == "system":
        if not config.get("schedule"):
            raise TriggerTypeError("system 类型任务必须包含 schedule 字段")
    else:  # user
        if "schedule" in config:
            raise TriggerTypeError("user 类型任务不允许 schedule 字段")

    return trigger_type
