import sqlite3
from pathlib import Path

from conftest import plan_for

from codex_agy.api import ExecutePlan, PlanInput
from codex_agy.models import State
from codex_agy.storage import Database
from codex_agy.supervisor import Supervisor


def test_existing_database_gets_metrics_tables(tmp_path):
    path = tmp_path / "old.sqlite3"
    db = Database(path)
    db.write("INSERT INTO idempotency VALUES('sample','key','fingerprint','{}')")
    db.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE execution_policies")
        conn.execute("DROP TABLE tool_calls")
    db = Database(path)
    try:
        assert db.one("SELECT count(*) FROM tool_calls")[0] == 0
        assert db.one("SELECT count(*) FROM execution_policies")[0] == 0
        assert db.one("SELECT response FROM idempotency WHERE key='key'")[0] == "{}"
    finally:
        db.close()


async def test_restart_recovers_rows_left_active_without_shutdown(settings, repo):
    settings.init()
    path = settings.data_dir / "supervisor.sqlite3"
    db = Database(path)
    supervisor = Supervisor(settings, db)
    await supervisor.submit_plan(PlanInput.model_validate({"plan": plan_for(repo)}))
    execution = await supervisor.execute_plan(ExecutePlan(plan_id="smoke"))
    task_id = execution["task_id"]
    second = await supervisor.execute_plan(ExecutePlan(plan_id="smoke"))
    cwd = Path(execution["workspace_path"])
    cwd.mkdir(parents=True)
    (cwd / "partial.py").write_text("partial output\n", encoding="utf-8")
    db.transition(task_id, State.STARTING)
    db.transition(task_id, State.RUNNING)
    db.close()

    db = Database(path)
    try:
        db.recover()
        task = db.task(task_id)
        assert task["status"] == State.INTERRUPTED
        assert db.task(second["task_id"])["status"] == State.QUEUED
        assert "SUPERVISOR_RESTARTED" in task["error"]
        assert (cwd / "partial.py").read_text(encoding="utf-8") == "partial output\n"
        latest = db.one(
            "SELECT previous,status FROM task_events WHERE task_id=? ORDER BY id DESC", (task_id,)
        )
        assert tuple(latest) == (State.RUNNING, State.INTERRUPTED)
    finally:
        db.close()
