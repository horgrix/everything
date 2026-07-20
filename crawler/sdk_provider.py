"""
SDK 数据提供者模块：封装第三方 SDK（如 akshare）调用。

将 SDK 返回的 DataFrame / list 统一转换为 list[dict]，
供 parser 和 engine 进行后续处理。

使用方式:
    provider = SDKProvider()
    rows = provider.call({
        "module": "akshare",
        "function": "stock_zh_a_hist",
        "params": {"symbol": "000001", "period": "daily", ...}
    })
    # rows = [{"日期": "2026-01-02", "开盘": 10.5, ...}, ...]
"""

import importlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SDKProvider:
    """
    同步 SDK 调用封装。

    设计原则：
    - 同步调用（适合低频定时任务场景，无需异步化开销）
    - 自动识别 DataFrame / list / dict 等返回类型
    - 统一输出为 list[dict] 格式
    """

    @staticmethod
    def call(provider_config: dict) -> list[dict]:
        """
        调用 SDK 函数并返回标准化的 list[dict]。

        参数:
            provider_config: YAML 中的 provider 配置块
                {
                    "module": "akshare",
                    "function": "stock_zh_a_hist",
                    "params": {"symbol": "000001", ...}
                }

        返回:
            list[dict] - 每行一个 dict
        """
        module_name = provider_config.get("module", "")
        func_name = provider_config.get("function", "")
        params = provider_config.get("params", {})

        if not module_name or not func_name:
            raise ValueError("provider 配置缺少 module 或 function")

        # 动态导入模块
        try:
            mod = importlib.import_module(module_name)
        except ImportError as e:
            raise ImportError(
                f"无法导入 SDK 模块 '{module_name}'，请确认已安装。"
                f"例如: pip install {module_name}"
            ) from e

        # 获取函数
        func = getattr(mod, func_name, None)
        if func is None:
            raise AttributeError(
                f"模块 '{module_name}' 中不存在函数 '{func_name}'"
            )

        # 调用
        logger.info("调用 SDK: %s.%s(%s)", module_name, func_name,
                     ", ".join(f"{k}={v}" for k, v in params.items()))
        try:
            result = func(**params)
        except Exception as e:
            logger.error("SDK 调用失败: %s.%s - %s", module_name, func_name, e)
            raise

        # 标准化为 list[dict]
        return SDKProvider._normalize(result)

    @staticmethod
    def _normalize(result: Any) -> list[dict]:
        """
        将各种 SDK 返回值统一转为 list[dict]。
        """
        # pandas DataFrame
        if hasattr(result, "to_dict"):
            try:
                return result.to_dict(orient="records")
            except Exception:
                pass

        # 已经是 list[dict]
        if isinstance(result, list):
            if len(result) == 0:
                return []
            if isinstance(result[0], dict):
                return result
            # list of something else, wrap
            return [{"value": item} for item in result]

        # 单个 dict，包装为列表
        if isinstance(result, dict):
            return [result]

        # 其他标量
        if result is None:
            return []
        return [{"value": result}]