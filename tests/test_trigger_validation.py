"""Tests for the shared trigger_type validation (loader + API wrapper)."""

import pytest

from task_manager.validation import TriggerTypeError, validate_trigger_type
from api.deps import validate_trigger_type as api_validate_trigger_type


class TestSharedValidator:
    """规则唯一实现 task_manager.validation.validate_trigger_type。"""

    def test_requires_trigger_type(self):
        with pytest.raises(TriggerTypeError):
            validate_trigger_type({})

    def test_rejects_invalid_type(self):
        with pytest.raises(TriggerTypeError):
            validate_trigger_type({"trigger_type": "bogus", "schedule": "x"})

    def test_system_requires_schedule(self):
        with pytest.raises(TriggerTypeError):
            validate_trigger_type({"trigger_type": "system"})

    def test_user_forbids_schedule(self):
        with pytest.raises(TriggerTypeError):
            validate_trigger_type({"trigger_type": "user", "schedule": "x"})

    def test_directory_mismatch(self):
        with pytest.raises(TriggerTypeError):
            validate_trigger_type(
                {"trigger_type": "user"}, expected_trigger_type="system"
            )

    def test_valid_system_and_user(self):
        assert validate_trigger_type(
            {"trigger_type": "system", "schedule": "x"},
            expected_trigger_type="system",
        ) == "system"
        assert validate_trigger_type(
            {"trigger_type": "user"}, expected_trigger_type="user"
        ) == "user"


class TestApiWrapper:
    """API 侧仅把 TriggerTypeError 转成 HTTP 400。"""

    def test_returns_validated_type(self):
        assert api_validate_trigger_type(
            {"trigger_type": "system", "schedule": "x"}
        ) == "system"

    def test_raises_http_400(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            api_validate_trigger_type({"trigger_type": "user", "schedule": "x"})
        assert exc.value.status_code == 400
