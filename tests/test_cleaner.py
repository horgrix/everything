"""Tests for the Cleaner module."""

import pytest
from crawler.cleaner import Cleaner


@pytest.fixture
def cleaner():
    return Cleaner()


class TestCleanField:
    """Unit tests for clean_field()."""

    def test_strip_default(self, cleaner):
        assert cleaner.clean_field("  hello  ", {}) == "hello"

    def test_strip_disabled(self, cleaner):
        result = cleaner.clean_field("  hello  ", {"strip": False})
        assert result == "  hello  "

    def test_to_number_int(self, cleaner):
        assert cleaner.clean_field("123", {"to_number": True}) == 123
        assert cleaner.clean_field("12,345", {"to_number": True}) == 12345

    def test_to_number_float(self, cleaner):
        assert cleaner.clean_field("3.14", {"to_number": True}) == 3.14

    def test_truncate_left(self, cleaner):
        assert cleaner.clean_field("abcdefgh", {"truncate_left": 3}) == "abc"

    def test_truncate_right(self, cleaner):
        assert cleaner.clean_field("abcdefgh", {"truncate_right": 2}) == "abcdef"

    def test_remove_html(self, cleaner):
        result = cleaner.clean_field("<p>Hello &amp; World</p>", {"remove_html": True})
        assert result == "Hello & World"

    def test_trim_whitespace(self, cleaner):
        result = cleaner.clean_field("hello   world", {"trim_whitespace": True})
        assert result == "hello world"

    def test_regex_extract(self, cleaner):
        result = cleaner.clean_field("Phone: 13812345678", {"regex_extract": r"(\d{11})"})
        assert result == "13812345678"

    def test_regex_replace(self, cleaner):
        result = cleaner.clean_field("hello WORLD", {
            "regex_replace": [{"pattern": "WORLD", "replacement": "world"}]
        })
        assert result == "hello world"

    def test_default_for_none(self, cleaner):
        result = cleaner.clean_field(None, {"default": "N/A"})
        assert result == "N/A"

    def test_to_datetime(self, cleaner):
        result = cleaner.clean_field("2026-07-18", {"to_datetime": True})
        assert result == "2026-07-18 00:00:00"


class TestNumberExprToInt:
    """Tests for _rule_number_expr_to_int Chinese number parsing."""

    def test_plain_number(self, cleaner):
        assert cleaner._rule_number_expr_to_int("1234", {}) == 1234

    def test_wan_unit(self, cleaner):
        assert cleaner._rule_number_expr_to_int("1234.56万", {}) == 12345600

    def test_yi_unit(self, cleaner):
        assert cleaner._rule_number_expr_to_int("5.67亿", {}) == 567000000


class TestMatchConditions:
    """Tests for where filter conditions."""

    def test_basic_equal(self, cleaner):
        assert cleaner._match_conditions("hello", {"where": {"op": "==", "value": "hello"}})
        assert not cleaner._match_conditions("hello", {"where": {"op": "==", "value": "world"}})

    def test_greater_than(self, cleaner):
        assert cleaner._match_conditions(100, {"where": {"op": ">", "value": 50}})
        assert not cleaner._match_conditions(10, {"where": {"op": ">", "value": 50}})

    def test_in_operator(self, cleaner):
        assert cleaner._match_conditions("a", {"where": {"op": "in", "value": ["a", "b"]}})
        assert not cleaner._match_conditions("c", {"where": {"op": "in", "value": ["a", "b"]}})

    def test_contains(self, cleaner):
        assert cleaner._match_conditions("hello world", {"where": {"op": "contains", "value": "world"}})
        assert not cleaner._match_conditions("hello", {"where": {"op": "contains", "value": "world"}})


class TestCleanBatch:
    """Integration tests for clean_batch with filtering."""

    def test_batch_no_filters(self, cleaner):
        fields = [
            {"name": "title", "strip": True},
            {"name": "count", "to_number": True},
        ]
        rows = [
            {"title": " a ", "count": "10"},
            {"title": " b ", "count": "20"},
        ]
        result = cleaner.clean_batch(rows, fields)
        assert len(result) == 2
        assert result[0]["count"] == 10

    def test_batch_with_where(self, cleaner):
        fields = [
            {"name": "value", "to_number": True,
             "where": {"op": ">", "value": 5}},
        ]
        rows = [{"value": "3"}, {"value": "8"}, {"value": "4"}]
        result = cleaner.clean_batch(rows, fields)
        assert len(result) == 1
        assert result[0]["value"] == 8
