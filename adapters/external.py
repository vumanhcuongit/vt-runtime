"""External adapter -- the platform boundary.

The runner talks to an adapter, never a named system. One interface, two
methods:

    execute(action, idempotency_key)  -> perform it, return a result id
    reconcile(idempotency_key)        -> yes / no / cannot_answer

Adding a new external target (review system, ATS, chat) is a new adapter
class plus one line in the CLI's registry -- never a runner change.

Putting `reconcile` in the interface states an honest fact: whether
recovery is possible depends on what the external system can answer, not
on how clever the runner is.
"""
import sqlite3

RECONCILE_YES = "yes"
RECONCILE_NO = "no"
RECONCILE_UNKNOWN = "cannot_answer"


class ExternalUnavailable(Exception):
    """The external system could not be reached."""


class ExternalAdapter:
    def execute(self, payload, idempotency_key: str) -> str:
        raise NotImplementedError

    def reconcile(self, idempotency_key: str):
        raise NotImplementedError


class _SqliteLedgerAdapter(ExternalAdapter):
    """A mock external system backed by its own SQLite file. Represents a
    target that supports lookup-by-key; `down=True` makes it unreachable."""

    id_prefix = "X-"
    id_start = 1

    def __init__(self, db_path: str, down: bool = False):
        self.down = down
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS records("
            " idempotency_key TEXT PRIMARY KEY, record_id TEXT NOT NULL)"
        )
        self.conn.commit()

    def find_by_key(self, key):
        row = self.conn.execute(
            "SELECT record_id FROM records WHERE idempotency_key=?", (key,)
        ).fetchone()
        return row["record_id"] if row else None

    def execute(self, payload, idempotency_key: str) -> str:
        # A real target would build its record from `payload` (e.g. a Slack
        # message body); this mock records an id keyed by idempotency_key and
        # ignores the body -- see README "what is mocked".
        if self.down:
            raise ExternalUnavailable("external system is down")
        existing = self.find_by_key(idempotency_key)
        if existing:
            return existing
        n = self.conn.execute("SELECT COUNT(*) AS c FROM records").fetchone()["c"]
        record_id = f"{self.id_prefix}{self.id_start + n}"
        self.conn.execute(
            "INSERT INTO records(idempotency_key, record_id) VALUES(?,?)",
            (idempotency_key, record_id),
        )
        self.conn.commit()
        return record_id

    def reconcile(self, idempotency_key: str):
        if self.down:
            return (RECONCILE_UNKNOWN, None)
        found = self.find_by_key(idempotency_key)
        if found:
            return (RECONCILE_YES, found)
        return (RECONCILE_NO, None)


class ReviewSystemAdapter(_SqliteLedgerAdapter):
    """Moza's review system: create_task -> T-991, T-992, ..."""
    id_prefix = "T-"
    id_start = 991


class AtsAdapter(_SqliteLedgerAdapter):
    """Helios's applicant tracking system: create_note -> N-501, N-502, ..."""
    id_prefix = "N-"
    id_start = 501
