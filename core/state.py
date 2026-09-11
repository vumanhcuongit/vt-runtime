"""Durable state in SQLite.

Two logical groups, kept in separate tables because they have different
lifecycles and are inspected separately (spec §10):
  - runs / steps    : runner state (what ran, what was decided)
  - external_actions: the ledger of intended business actions

SQLite gives us atomic writes for free. In a crash-recovery demo, a
half-written JSON file would be the most ironic failure possible; a
committed SQLite transaction is durable across os._exit().
"""
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    vt            TEXT NOT NULL,
    workflow      TEXT NOT NULL,
    status        TEXT NOT NULL,          -- running | completed | stopped
    stopped_reason TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    step_name  TEXT NOT NULL,
    item_id    TEXT,                       -- NULL for run-level steps (fetch)
    result     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, step_name, item_id)     -- re-running a recorded step is a no-op
);

CREATE TABLE IF NOT EXISTS external_actions (
    idempotency_key TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    approval_status TEXT NOT NULL,         -- pending | approved
    action_status   TEXT NOT NULL,         -- none | intent | committed
    result          TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- runs ----
    def create_run(self, run_id, vt, workflow):
        self.conn.execute(
            "INSERT OR IGNORE INTO runs(run_id, vt, workflow, status, created_at)"
            " VALUES(?,?,?,?,?)",
            (run_id, vt, workflow, "running", _now()),
        )
        self.conn.commit()

    def get_run(self, run_id):
        row = self.conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def set_run_status(self, run_id, status, stopped_reason=None):
        self.conn.execute(
            "UPDATE runs SET status=?, stopped_reason=? WHERE run_id=?",
            (status, stopped_reason, run_id),
        )
        self.conn.commit()

    def list_runs(self):
        rows = self.conn.execute("SELECT * FROM runs ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    # ---- steps ----
    def record_step(self, run_id, step_name, item_id, result, detail):
        self.conn.execute(
            "INSERT OR IGNORE INTO steps(run_id, step_name, item_id, result, detail, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (run_id, step_name, item_id, result, detail, _now()),
        )
        self.conn.commit()

    def upsert_step(self, run_id, step_name, item_id, result, detail):
        # Like record_step, but the LATEST write wins. Used for external
        # steps, whose row transitions (intent -> reconciled -> committed):
        # first-write-wins would leave the audit trail showing a stale state
        # that contradicts the ledger after a recovery.
        self.conn.execute(
            "INSERT INTO steps(run_id, step_name, item_id, result, detail, created_at)"
            " VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(run_id, step_name, item_id) DO UPDATE SET"
            " result=excluded.result, detail=excluded.detail",
            (run_id, step_name, item_id, result, detail, _now()),
        )
        self.conn.commit()

    def get_step(self, run_id, step_name, item_id):
        row = self.conn.execute(
            "SELECT * FROM steps WHERE run_id=? AND step_name=? AND item_id IS ?",
            (run_id, step_name, item_id),
        ).fetchone()
        return dict(row) if row else None

    def list_steps(self, run_id):
        rows = self.conn.execute(
            "SELECT * FROM steps WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- external_actions ----
    def create_action(self, key, run_id, approval_status, action_status):
        now = _now()
        self.conn.execute(
            "INSERT OR IGNORE INTO external_actions"
            "(idempotency_key, run_id, approval_status, action_status, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?)",
            (key, run_id, approval_status, action_status, now, now),
        )
        self.conn.commit()

    def get_action(self, key):
        row = self.conn.execute(
            "SELECT * FROM external_actions WHERE idempotency_key=?", (key,)
        ).fetchone()
        return dict(row) if row else None

    def set_action_status(self, key, action_status, result=None):
        self.conn.execute(
            "UPDATE external_actions SET action_status=?, result=COALESCE(?, result),"
            " updated_at=? WHERE idempotency_key=?",
            (action_status, result, _now(), key),
        )
        self.conn.commit()

    def set_approval(self, key, approval_status):
        self.conn.execute(
            "UPDATE external_actions SET approval_status=?, updated_at=? WHERE idempotency_key=?",
            (approval_status, _now(), key),
        )
        self.conn.commit()

    def list_actions(self):
        rows = self.conn.execute(
            "SELECT * FROM external_actions ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]
