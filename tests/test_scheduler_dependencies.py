"""Tests for scheduler task dependencies (depends_on)."""

import asyncio

from scheduler.scheduler import CrawlScheduler


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


def _scheduler(db, engine):
    return CrawlScheduler(config_dir="unused", db=db, engine=engine)


def _build(sched, db, specs):
    """specs: list[(name, depends_on_list)] —— 注册到 db 并填充 _tasks 与反向索引。"""
    for name, deps in specs:
        task_id = db.upsert_task(name, "db", "dummy_table", "", "", "system")
        sched._tasks[name] = {"name": name, "_task_id": task_id, "depends_on": deps or []}
    for name, deps in specs:
        for upstream in deps:
            sched._dependents.setdefault(upstream, []).append(sched._tasks[name])


def test_single_upstream_triggers_downstream(db):
    engine = FakeEngine()
    sched = _scheduler(db, engine)
    _build(sched, db, [("calc", []), ("summary", ["calc"])])
    asyncio.run(sched._run_task(sched._tasks["calc"]))
    assert engine.calls == ["calc", "summary"]


def test_upstream_failure_blocks_downstream(db):
    engine = FakeEngine(fail_names={"calc"})
    sched = _scheduler(db, engine)
    _build(sched, db, [("calc", []), ("summary", ["calc"])])
    asyncio.run(sched._run_task(sched._tasks["calc"]))
    assert engine.calls == ["calc"]
    # 下游自己的 cron 兜底触发也不会跑（上游无成功记录）
    asyncio.run(sched._run_task(sched._tasks["summary"]))
    assert engine.calls == ["calc"]


def test_multiple_upstream_and(db):
    engine = FakeEngine()
    sched = _scheduler(db, engine)
    _build(sched, db, [("a", []), ("b", []), ("c", ["a", "b"])])
    asyncio.run(sched._run_task(sched._tasks["a"]))
    assert engine.calls == ["a"]
    asyncio.run(sched._run_task(sched._tasks["b"]))
    assert engine.calls == ["a", "b", "c"]


def test_cron_fallback_does_not_rerun(db):
    engine = FakeEngine()
    sched = _scheduler(db, engine)
    _build(sched, db, [("calc", []), ("summary", ["calc"])])
    asyncio.run(sched._run_task(sched._tasks["calc"]))
    assert engine.calls == ["calc", "summary"]
    # 模拟 summary 的 cron 到点再次触发（上游未再次成功 → 不应重复执行）
    asyncio.run(sched._run_task(sched._tasks["summary"]))
    assert engine.calls == ["calc", "summary"]


def test_reentrancy_guard(db):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def fake_run(task_config, db):
        calls.append(task_config.get("name"))
        started.set()
        await release.wait()
        return {"new": 0, "updated": 0, "skipped": 0, "error": None}

    class SlowEngine:
        async def run(self, task_config, db):
            return await fake_run(task_config, db)

    async def main():
        sched = _scheduler(db, SlowEngine())
        _build(sched, db, [("calc", [])])
        t1 = asyncio.create_task(sched._run_task(sched._tasks["calc"]))
        await started.wait()
        # calc 正在运行，第二次触发应被防重入跳过
        await sched._run_task(sched._tasks["calc"])
        release.set()
        await t1

    asyncio.run(main())
    assert calls == ["calc"]
