"""Tests for group-level trigger (trigger_group)."""

import asyncio

from scheduler.scheduler import CrawlScheduler
from task_manager.loader import TaskLoader


class FakeEngine:
    """记录调用顺序的假引擎，可选按任务名注入失败。"""

    def __init__(self, fail_names=None):
        self.calls = []
        self.fail_names = set(fail_names or [])

    async def run(self, task_config, db):
        name = task_config.get("name")
        self.calls.append(name)
        if name in self.fail_names:
            return {"new": 0, "updated": 0, "skipped": 0, "error": "boom"}
        return {"new": 1, "updated": 0, "skipped": 0, "error": None}


def _build(sched, db, specs):
    """specs: list[(name, depends_on_list, group)]"""
    for name, deps, group in specs:
        task_id = db.upsert_task(name, "db", "dummy_table", "", "", "system", group)
        sched._tasks[name] = {
            "name": name, "_task_id": task_id, "depends_on": deps or [], "_group": group,
        }
    for name, deps, group in specs:
        for upstream in deps:
            sched._dependents.setdefault(upstream, []).append(sched._tasks[name])


def test_trigger_group_runs_roots_and_cascades(db, tmp_path):
    engine = FakeEngine()
    sched = CrawlScheduler(config_dir="unused", db=db, engine=engine, log_dir=str(tmp_path))
    _build(sched, db, [("a", [], "g1"), ("b", ["a"], "g1"), ("c", [], "g2")])
    asyncio.run(sched.trigger_group("g1"))
    assert engine.calls == ["a", "b"]


def test_trigger_group_other_group_not_triggered(db, tmp_path):
    engine = FakeEngine()
    sched = CrawlScheduler(config_dir="unused", db=db, engine=engine, log_dir=str(tmp_path))
    _build(sched, db, [("a", [], "g1"), ("c", [], "g2")])
    asyncio.run(sched.trigger_group("g2"))
    assert engine.calls == ["c"]


def test_trigger_group_loads_when_tasks_empty(db, tmp_path):
    """_tasks 为空时先从 loader 加载（fallback 路径）。"""
    root = tmp_path / "tasks"
    (root / "g1").mkdir(parents=True)
    (root / "g1" / "sys.yaml").write_text(
        "name: sys_a\n"
        "type: api\n"
        "trigger_type: system\n"
        "schedule: \"0 * * * *\"\n"
        "outputs:\n"
        "  - target_table: t_a\n"
        "    table_schema:\n"
        "      columns:\n"
        "        - name: id\n"
        "          type: INTEGER\n"
        "    parser:\n"
        "      type: sdk_mapping\n"
        "      fields:\n"
        "        - name: id\n"
        "          source: id\n",
        encoding="utf-8",
    )
    engine = FakeEngine()
    loader = TaskLoader(str(root), db)
    sched = CrawlScheduler(config_dir=str(root), db=db, engine=engine, loader=loader, log_dir=str(tmp_path))
    asyncio.run(sched.trigger_group("g1"))
    assert engine.calls == ["sys_a"]
