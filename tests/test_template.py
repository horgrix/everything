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
