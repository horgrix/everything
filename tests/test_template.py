"""Tests for URLTemplate variable resolution."""

import pytest
from crawler.template import URLTemplate
from datetime import datetime, timedelta


class TestURLTemplate:
    """Unit tests for URLTemplate.resolve()."""

    def test_today(self):
        result = URLTemplate.resolve("{today}")
        expected = datetime.now().strftime("%Y-%m-%d")
        assert result == expected

    def test_today_custom_format(self):
        result = URLTemplate.resolve("{today:%Y%m%d}")
        expected = datetime.now().strftime("%Y%m%d")
        assert result == expected

    def test_yesterday(self):
        result = URLTemplate.resolve("{yesterday}")
        expected = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        assert result == expected

    def test_days_ago(self):
        result = URLTemplate.resolve("{days_ago:7}")
        expected = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        assert result == expected

    def test_days_ago_with_format(self):
        result = URLTemplate.resolve("{days_ago:30:%Y%m%d}")
        expected = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        assert result == expected

    def test_timestamp(self):
        result = URLTemplate.resolve("{timestamp}")
        assert result.isdigit()
        assert abs(int(result) - int(datetime.now().timestamp())) < 2

    def test_task_name(self):
        result = URLTemplate.resolve("{task_name}", context={"task_name": "my_task"})
        assert result == "my_task"

    def test_custom_context_var(self):
        result = URLTemplate.resolve("{region}", context={"region": "CN"})
        assert result == "CN"

    def test_url_with_vars(self):
        result = URLTemplate.resolve(
            "https://api.example.com?date={today}&region={region}",
            context={"task_name": "test", "region": "global"}
        )
        expected = f"https://api.example.com?date={datetime.now().strftime('%Y-%m-%d')}&region=global"
        assert result == expected

    def test_nested_vars(self):
        """Variables used multiple times in the same template."""
        result = URLTemplate.resolve("{region}/{region}", context={"region": "CN"})
        assert result == "CN/CN"

    def test_no_context(self):
        """Resolve without context dict."""
        result = URLTemplate.resolve("{today}")
        assert result == datetime.now().strftime("%Y-%m-%d")


class TestEnvSubstitution:
    """${ENV_VAR} 替换（先于 {var} 正则，缺失时保留原样）。"""

    def test_env_var_substituted(self, monkeypatch):
        monkeypatch.setenv("TAPTAP_KEY", "abc123")
        assert URLTemplate.resolve("${TAPTAP_KEY}") == "abc123"

    def test_env_var_in_url(self, monkeypatch):
        monkeypatch.setenv("API_HOST", "example.com")
        result = URLTemplate.resolve(
            "https://${API_HOST}/data?date={today}",
            context={"today": "IGNORED"},
        )
        expected = f"https://example.com/data?date={datetime.now().strftime('%Y-%m-%d')}"
        assert result == expected

    def test_env_and_template_do_not_interfere(self, monkeypatch):
        monkeypatch.setenv("MY_PATH", "v1")
        result = URLTemplate.resolve(
            "${MY_PATH}/{region}/${MY_PATH}", context={"region": "cn"}
        )
        assert result == "v1/cn/v1"

    def test_missing_env_preserved(self, monkeypatch):
        monkeypatch.delenv("MISSING_ENV_VAR", raising=False)
        result = URLTemplate.resolve("a-${MISSING_ENV_VAR}-b")
        assert result == "a-${MISSING_ENV_VAR}-b"

    def test_missing_env_does_not_break_other_vars(self, monkeypatch):
        monkeypatch.delenv("MISSING_ENV_VAR", raising=False)
        result = URLTemplate.resolve(
            "${MISSING_ENV_VAR}/{region}", context={"region": "cn"}
        )
        assert result == "${MISSING_ENV_VAR}/cn"

    def test_env_ignored_when_lowercase_pattern(self, monkeypatch):
        """普通 {var} 不受影响。"""
        monkeypatch.setenv("lower_var", "should_not_be_used")
        assert URLTemplate.resolve("{lower_var}") == "{lower_var}"

