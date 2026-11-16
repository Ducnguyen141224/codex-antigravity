import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from codex_agy.models import ACTIVE, Problem, State

SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_policies (
    execution_id TEXT PRIMARY KEY, max_review_rounds INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY, execution_id TEXT NOT NULL, method TEXT NOT NULL,
    source TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY, project TEXT NOT NULL, repo_key TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_versions (
    plan_id TEXT NOT NULL REFERENCES plans(id), version INTEGER NOT NULL, body TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (plan_id, version)
);
CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, version INTEGER NOT NULL,
    worktree TEXT NOT NULL UNIQUE, base TEXT NOT NULL, head TEXT NOT NULL, automatic INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (plan_id, version) REFERENCES plan_versions(plan_id, version)
);
CREATE TABLE IF NOT EXISTS milestones (
    id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id),
    milestone_id TEXT NOT NULL, ordinal INTEGER NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0, retrying INTEGER NOT NULL DEFAULT 0,
    round INTEGER NOT NULL DEFAULT 0,
    session_id TEXT, review_revision TEXT, error TEXT, result TEXT, input_base TEXT,
    UNIQUE(execution_id, milestone_id)
);
CREATE INDEX IF NOT EXISTS milestone_queue ON milestones(status, updated_at);
CREATE TABLE IF NOT EXISTS tasks (
    milestone TEXT NOT NULL REFERENCES milestones(id), task_id TEXT NOT NULL,
    PRIMARY KEY (milestone, task_id)
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES milestones(id),
    started_at TEXT NOT NULL, ended_at TEXT, PRIMARY KEY (id, task_id, started_at)
);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES milestones(id),
    previous TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, details TEXT
);
CREATE TABLE IF NOT EXISTS review_rounds (
    task_id TEXT NOT NULL REFERENCES milestones(id), round INTEGER NOT NULL,
    base TEXT NOT NULL, revision TEXT NOT NULL, diff_hash TEXT NOT NULL, metadata TEXT NOT NULL,
    decision TEXT, review_summary TEXT, PRIMARY KEY (task_id, round)
);
CREATE TABLE IF NOT EXISTS review_findings (
    task_id TEXT NOT NULL, round INTEGER NOT NULL, ordinal INTEGER NOT NULL, body TEXT NOT NULL,
    FOREIGN KEY (task_id, round) REFERENCES review_rounds(task_id, round),
    PRIMARY KEY (task_id, round, ordinal)
);
CREATE TABLE IF NOT EXISTS verification_results (
    task_id TEXT NOT NULL, round INTEGER NOT NULL, ordinal INTEGER NOT NULL, body TEXT NOT NULL,
    FOREIGN KEY (task_id, round) REFERENCES review_rounds(task_id, round),
    PRIMARY KEY (task_id, round, ordinal)
);
CREATE TABLE IF NOT EXISTS artifacts (
    task_id TEXT NOT NULL, round INTEGER NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL,
    FOREIGN KEY (task_id, round) REFERENCES review_rounds(task_id, round),
    PRIMARY KEY (task_id, round, name)
);
CREATE TABLE IF NOT EXISTS permissions (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES milestones(id), request TEXT NOT NULL,
    decision TEXT, created_at TEXT NOT NULL, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS idempotency (
    operation TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL, response TEXT NOT NULL,
    PRIMARY KEY (operation, key)
);
PRAGMA user_version = 1;
"""

TRANSITIONS = {
    State.CREATED: {State.VALIDATED},
    State.VALIDATED: {State.QUEUED, State.CANCELLED},
    State.QUEUED: {State.STARTING, State.CANCELLED},
    State.STARTING: {
        State.RUNNING,
        State.BLOCKED,
        State.FAILED,
        State.TIMED_OUT,
        State.CANCELLED,
        State.INTERRUPTED,
    },
    State.RUNNING: {
        State.WAITING_PERMISSION,
        State.WAITING_INPUT,
        State.VERIFYING,
        State.FAILED,
        State.BLOCKED,
        State.TIMED_OUT,
        State.CANCELLED,
        State.INTERRUPTED,
    },
    State.WAITING_PERMISSION: {
        State.RUNNING,
        State.BLOCKED,
        State.FAILED,
        State.TIMED_OUT,
        State.CANCELLED,
        State.INTERRUPTED,
    },
    State.WAITING_INPUT: {
        State.BLOCKED,
        State.FAILED,
        State.TIMED_OUT,
        State.CANCELLED,
        State.INTERRUPTED,
    },
    State.VERIFYING: {
        State.REVIEW_REQUIRED,
        State.FIX_REQUESTED,
        State.FAILED,
        State.BLOCKED,
        State.TIMED_OUT,
        State.CANCELLED,
        State.INTERRUPTED,
    },
    State.REVIEW_REQUIRED: {State.ACCEPTED, State.FIX_REQUESTED, State.BLOCKED, State.CANCELLED},
    State.FIX_REQUESTED: {State.STARTING, State.CANCELLED},
    **{
        state: {State.QUEUED}
        for state in (
            State.BLOCKED,
            State.FAILED,
            State.TIMED_OUT,
            State.INTERRUPTED,
            State.CANCELLED,
        )
    },
}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode(value, default=None):
    return json.loads(value) if value is not None else default


class Database:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version in {0, 1}:
            # Additive tables also need to be installed for databases from older releases.
            self.conn.executescript(f"BEGIN IMMEDIATE;\n{SCHEMA}\nCOMMIT;")
        elif version != 1:
            self.close()
            raise Problem("DB_VERSION", f"Unsupported SQLite schema version {version}.")

    def close(self):
        self.conn.close()

    @contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def one(self, sql, values=()):
        return self.conn.execute(sql, values).fetchone()

    def all(self, sql, values=()):
        return self.conn.execute(sql, values).fetchall()

    def write(self, sql, values=()):
        return self.conn.execute(sql, values)

    def task(self, task_id: str):
        row = self.one(
            """SELECT m.*, e.worktree, e.base, e.head, e.plan_id, e.version, e.automatic,
                      p.project, p.repo_key
               FROM milestones m JOIN executions e ON e.id=m.execution_id
               JOIN plans p ON p.id=e.plan_id WHERE m.id=?""",
            (task_id,),
        )
        if row is None:
            raise Problem("TASK_NOT_FOUND", f"Unknown task {task_id}.")
        return row

    def transition(self, task_id: str, target: State, error: dict | None = None):
        row = self.task(task_id)
        source = State(row["status"])
        if target not in TRANSITIONS.get(source, set()):
            raise Problem("INVALID_STATE", f"Cannot transition from {source} to {target}.")
        stamp = now()
        finished = (
            stamp
            if target
            not in ACTIVE
            | {
                State.CREATED,
                State.VALIDATED,
                State.QUEUED,
                State.FIX_REQUESTED,
            }
            else None
        )
        started = stamp if target is State.STARTING else row["started_at"]
        self.write(
            """UPDATE milestones SET status=?, updated_at=?, started_at=?, finished_at=?, error=?
               WHERE id=?""",
            (target, stamp, started, finished, encode(error) if error else None, task_id),
        )
        self.write(
            """INSERT INTO task_events(task_id,previous,status,created_at,details)
               VALUES(?,?,?,?,?)""",
            (task_id, source, target, stamp, encode(error) if error else None),
        )

    def recover(self):
        with self.transaction():
            for row in self.all("SELECT id,status FROM milestones"):
                if row["status"] in ACTIVE:
                    self.transition(
                        row["id"],
                        State.INTERRUPTED,
                        {
                            "code": "SUPERVISOR_RESTARTED",
                            "message": "Inspect retained workspace before explicitly retrying.",
                        },
                    )
            stamp = now()
            self.write(
                "UPDATE permissions SET decision='interrupted',decided_at=? WHERE decision IS NULL",
                (stamp,),
            )
            self.write("UPDATE sessions SET ended_at=? WHERE ended_at IS NULL", (stamp,))

    def cached(self, operation, key, fingerprint):
        if key is None:
            return None
        row = self.one("SELECT * FROM idempotency WHERE operation=? AND key=?", (operation, key))
        if row is None:
            return None
        if row["fingerprint"] != fingerprint:
            raise Problem("IDEMPOTENCY_CONFLICT", "This key was used with a different request.")
        return decode(row["response"])

    def cache(self, operation, key, fingerprint, response):
        if key is not None:
            self.write(
                "INSERT INTO idempotency VALUES(?,?,?,?)",
                (operation, key, fingerprint, encode(response)),
            )
