"""Tests for Parser when-condition field extraction (runtime branching)."""

from crawler.parser import Parser


def _parse(rows, fields):
    parser = Parser()
    return parser.parse_rows(rows, {"type": "sdk_mapping", "fields": fields})


class TestWhenBranching:
    def test_branch_by_type_field(self):
        """根据当前行的 type 字段，动态选择不同的 path。"""
        raw = [
            {"type": "app", "app": {"id": 1, "title": "A"}},
            {"type": "moment", "moment": {"app": {"id": 2, "title": "B"}}},
        ]
        fields = [
            {
                "name": "app_id",
                "when": [
                    {"field": "type", "op": "==", "value": "app",
                     "then": {"path": "app.id"}},
                    {"field": "type", "op": "==", "value": "moment",
                     "then": {"path": "moment.app.id"}},
                ],
            },
            {
                "name": "title",
                "when": [
                    {"field": "type", "op": "==", "value": "app",
                     "then": {"path": "app.title"}},
                    {"field": "type", "op": "==", "value": "moment",
                     "then": {"path": "moment.app.title"}},
                ],
            },
        ]
        rows = _parse(raw, fields)
        assert rows == [
            {"app_id": 1, "title": "A"},
            {"app_id": 2, "title": "B"},
        ]

    def test_otherwise_fallback(self):
        """when 都不匹配时用 otherwise 兜底。"""
        raw = [
            {"type": "app", "app": {"id": 1}},
            {"type": "unknown"},
        ]
        fields = [
            {
                "name": "app_id",
                "when": [
                    {"field": "type", "op": "==", "value": "app",
                     "then": {"path": "app.id"}},
                ],
                "otherwise": {"value": "默认"},
            },
        ]
        rows = _parse(raw, fields)
        assert rows[0]["app_id"] == 1
        assert rows[1]["app_id"] == "默认"

    def test_fallthrough_to_own_path(self):
        """when 不匹配且无 otherwise → 降级到字段自身的 path。"""
        raw = [{"type": "app", "fallback": 99}]
        fields = [
            {
                "name": "app_id",
                "path": "fallback",
                "when": [
                    {"field": "type", "op": "==", "value": "moment",
                     "then": {"path": "moment.id"}},
                ],
            },
        ]
        rows = _parse(raw, fields)
        assert rows[0]["app_id"] == 99

    def test_op_in(self):
        """支持 in 运算符。"""
        raw = [{"type": "app"}]
        fields = [
            {
                "name": "kind",
                "when": [
                    {"field": "type", "op": "in", "value": ["app", "moment"],
                     "then": {"value": "游戏"}},
                ],
                "otherwise": {"value": "其他"},
            },
        ]
        rows = _parse(raw, fields)
        assert rows[0]["kind"] == "游戏"

    def test_nested_when(self):
        """then 内嵌字段配置可以再嵌套 when。"""
        raw = [
            {"type": "app", "app": {"id": 1}, "extra": {"id": 100}},
            {"type": "moment", "app": {"id": 2}, "extra": {"id": 200}},
        ]
        fields = [
            {
                "name": "app_id",
                "when": [
                    {
                        "field": "type", "op": "==", "value": "app",
                        "then": {
                            "when": [
                                {"field": "extra.id", "op": ">", "value": 50,
                                 "then": {"path": "extra.id"}},
                            ],
                            "otherwise": {"path": "app.id"},
                        },
                    },
                ],
                "otherwise": {"path": "app.id"},
            },
        ]
        rows = _parse(raw, fields)
        # type=app 且 extra.id=100>50 → 取 extra.id
        assert rows[0]["app_id"] == 100
        # type=moment 不匹配 when → otherwise 取 app.id
        assert rows[1]["app_id"] == 2


class TestPaginationIndexOffset:
    """翻页时 index 字段跨页连续计数（page_size / index_offset）。"""

    ROWS = [{"ad_id": "1;", "kw": "a"}, {"ad_id": "2;", "kw": "b"},
            {"ad_id": "3;", "kw": "c"}]
    FIELDS = [
        {"name": "keyword", "path": "kw"},
        {"name": "position", "index": 1, "to_number": True},
    ]

    def _positions(self, config, context=None):
        parser = Parser()
        rows = parser.parse_rows(self.ROWS, config, context=context or {})
        return [r["position"] for r in rows]

    def _config(self, **extra):
        return {"type": "sdk_mapping", "fields": self.FIELDS, **extra}

    def test_default_restarts_each_page(self):
        """不配分页时保持旧行为：每页从 1 重新数。"""
        assert self._positions(self._config()) == [1, 2, 3]
        assert self._positions(self._config(), {"page": "2"}) == [1, 2, 3]

    def test_page_size_with_page_continues_across_pages(self):
        config = self._config(page_size=3)
        assert self._positions(config, {"page": "1"}) == [1, 2, 3]
        assert self._positions(config, {"page": "2"}) == [4, 5, 6]
        assert self._positions(config, {"page": "3"}) == [7, 8, 9]

    def test_page_size_zero_based_index(self):
        config = {"type": "sdk_mapping", "page_size": 3,
                  "fields": [{"name": "position", "index": 0}]}
        parser = Parser()
        rows = parser.parse_rows(self.ROWS, config, context={"page": "2"})
        assert [r["position"] for r in rows] == [3, 4, 5]

    def test_explicit_index_offset_in_config(self):
        assert self._positions(self._config(index_offset=10)) == [11, 12, 13]

    def test_index_offset_from_context_template(self):
        config = self._config(index_offset="{page_start}")
        assert self._positions(config, {"page_start": "30"}) == [31, 32, 33]

    def test_config_offset_wins_over_page_size(self):
        config = self._config(page_size=3, index_offset=100)
        assert self._positions(config, {"page": "2"}) == [101, 102, 103]

    def test_index_template_also_offset(self):
        config = {"type": "sdk_mapping", "page_size": 3,
                  "fields": [{"name": "seq", "value": "{index}"}]}
        parser = Parser()
        rows = parser.parse_rows(self.ROWS, config, context={"page": "2"})
        assert [r["seq"] for r in rows] == ["3", "4", "5"]

    def test_non_numeric_page_ignored(self):
        """page 不是数字时退回 0，不抛异常。"""
        config = self._config(page_size=3)
        assert self._positions(config, {"page": "abc"}) == [1, 2, 3]


class TestFromStylePagination:
    """真实场景：iterate 用 from=0/10/20 控制偏移（limit=10 的接口）。"""

    def _positions(self, from_value):
        rows = [{"title": f"app{i}"} for i in range(10)]
        config = {"type": "sdk_mapping", "index_offset": "{from}",
                  "fields": [{"name": "position", "index": 1, "to_number": True}]}
        parser = Parser()
        return [r["position"] for r in parser.parse_rows(rows, config, context={"from": str(from_value)})]

    def test_from_0_10_20_continuous(self):
        assert self._positions(0) == list(range(1, 11))
        assert self._positions(10) == list(range(11, 21))
        assert self._positions(20) == list(range(21, 31))

    def test_from_offset_keeps_unique_per_hour(self):
        """跨页 position 不重复，配 (crawled_at, position) 唯一索引不会撞键。"""
        all_positions = self._positions(0) + self._positions(10) + self._positions(20)
        assert len(all_positions) == len(set(all_positions)) == 30
