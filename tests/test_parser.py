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
