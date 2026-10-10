"""Tests for group-based task loading and trigger_type validation."""

from task_manager.loader import TaskLoader


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _system_task(name, table="t"):
    return f"""name: {name}
type: api
trigger_type: system
schedule: "0 * * * *"
outputs:
  - target_table: {table}
    table_schema:
      columns:
        - name: id
          type: INTEGER
    parser:
      type: sdk_mapping
      fields:
        - name: id
          source: id
"""


def _user_task(name, table="t"):
    return f"""name: {name}
type: api
trigger_type: user
url: https://example.com
outputs:
  - target_table: {table}
    table_schema:
      columns:
        - name: id
          type: INTEGER
    parser:
      type: sdk_mapping
      fields:
        - name: id
          source: id
"""


def test_group_dirs_and_trigger_type(db, tmp_path):
    """组根=system、manual=user、group=目录名；script/ 和 README.md 被跳过。"""
    root = tmp_path / "tasks"
    _write(root / "g1" / "README.md", "# g1\n")
    _write(root / "g1" / "script" / "x.yaml", _system_task("should_skip", "t_skip"))
    _write(root / "g1" / "sys.yaml", _system_task("sys_a", "t_sys_a"))
    _write(root / "g1" / "manual" / "usr.yaml", _user_task("usr_a", "t_usr_a"))
    _write(root / "g2" / "sys2.yaml", _system_task("sys_b", "t_sys_b"))
    _write(root / "README.md", "# top-level file, not a dir\n")

    loader = TaskLoader(str(root), db)
    tasks = loader.load_all()

    names = {t["name"]: t for t in tasks}
    assert set(names) == {"sys_a", "usr_a", "sys_b"}
    assert names["sys_a"].get("_group") == "g1"
    assert names["sys_a"].get("_trigger_type") == "system"
    assert names["usr_a"].get("_group") == "g1"
    assert names["usr_a"].get("_trigger_type") == "user"
    assert names["sys_b"].get("_group") == "g2"


def test_trigger_type_mismatch_skipped(db, tmp_path):
    """trigger_type 与目录位置不符时跳过该任务。"""
    root = tmp_path / "tasks"
    _write(root / "g1" / "bad.yaml", _user_task("bad_user_in_root", "t_bad"))
    _write(root / "g1" / "manual" / "bad2.yaml", _system_task("bad_sys_in_manual", "t_bad2"))
    _write(root / "g1" / "ok.yaml", _system_task("ok_sys", "t_ok"))

    loader = TaskLoader(str(root), db)
    tasks = loader.load_all()
    assert {t["name"] for t in tasks} == {"ok_sys"}


def test_group_recorded_in_db(db, tmp_path):
    """upsert_task 写入 group 列。"""
    root = tmp_path / "tasks"
    _write(root / "g1" / "sys.yaml", _system_task("sys_a", "t_sys_a"))

    loader = TaskLoader(str(root), db)
    loader.load_all()

    row = db.conn.execute(
        'SELECT "group" FROM crawl_tasks WHERE task_name=?', ("sys_a",)
    ).fetchone()
    assert row["group"] == "g1"
