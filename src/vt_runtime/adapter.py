"""The platform boundary.

The runner talks to an adapter, never to a named system. One interface,
two methods:
    execute(action, key)  -> perform the action, return its result id
    reconcile(key)        -> "did this already happen?"  yes / no / cannot_answer

Swapping the target (review system, ATS, chat) is a new adapter + config,
never a runner change.

reconcile is the weakest assumption in the design: it only works if the
external system can answer "does an action with key K exist?". Production
should prefer a target that accepts an idempotency key natively so the
SYSTEM deduplicates. See README "Known limitations".
"""
import sqlite3

RECONCILE_YES = "yes"
RECONCILE_NO = "no"
RECONCILE_UNKNOWN = "cannot_answer"


class ExternalUnavailable(Exception):
    """The external system could not be reached."""


class ExternalAdapter:
    def execute(self, action: dict, idempotency_key: str) -> str:
        raise NotImplementedError

    def reconcile(self, idempotency_key: str):
        raise NotImplementedError


class ReviewSystemAdapter(ExternalAdapter):
    def __init__(self, db_path: str, down: bool = False):
        self.down = down
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS tasks("
            " idempotency_key TEXT PRIMARY KEY, task_id TEXT NOT NULL)"
        )
        self.conn.commit()

    def find_task_by_key(self, key):
        row = self.conn.execute(
            "SELECT task_id FROM tasks WHERE idempotency_key=?", (key,)
        ).fetchone()
        return row["task_id"] if row else None

    def execute(self, action: dict, idempotency_key: str) -> str:
        if self.down:
            raise ExternalUnavailable("review_system is down")
        existing = self.find_task_by_key(idempotency_key)
        if existing:
            return existing
        n = self.conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
        task_id = f"T-{991 + n}"
        self.conn.execute(
            "INSERT INTO tasks(idempotency_key, task_id) VALUES(?,?)",
            (idempotency_key, task_id),
        )
        self.conn.commit()
        return task_id

    def reconcile(self, idempotency_key: str):
        if self.down:
            return (RECONCILE_UNKNOWN, None)
        found = self.find_task_by_key(idempotency_key)
        if found:
            return (RECONCILE_YES, found)
        return (RECONCILE_NO, None)
