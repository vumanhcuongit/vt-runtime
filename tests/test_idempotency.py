"""The property the crash scenario is really about: a derived key that is
stable across retries of a run and distinct across separate runs."""
import os
import sqlite3
import tempfile
import unittest

from core.actions import derive_key, perform_external
from core.runner import load_config, Runner
from core.state import Store
from adapters.external import (
    ReviewSystemAdapter, AtsAdapter, RECONCILE_YES, RECONCILE_NO,
)
from adapters.model import ReplayModel


class PlainInsertAdapter:
    """A NON-idempotent external target: execute() always inserts a fresh row
    (no primary-key dedup), while reconcile() still answers truthfully by key.
    If the runner ever blindly re-executes, this produces a duplicate -- so a
    test against it proves the RUNNER's logic prevents duplicates, not the
    mock's own primary key."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE records("
                          " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                          " idempotency_key TEXT, record_id TEXT)")
        self.conn.commit()

    def execute(self, action, key):
        rid = f"X-{self.count() + 1}"
        self.conn.execute("INSERT INTO records(idempotency_key, record_id) VALUES(?,?)",
                          (key, rid))
        self.conn.commit()
        return rid

    def reconcile(self, key):
        row = self.conn.execute(
            "SELECT record_id FROM records WHERE idempotency_key=? LIMIT 1",
            (key,)).fetchone()
        return (RECONCILE_YES, row["record_id"]) if row else (RECONCILE_NO, None)

    def count(self):
        return self.conn.execute("SELECT COUNT(*) c FROM records").fetchone()["c"]

MOZA = "workflows/moza_song_screening/config.json"


def build(cfg, tmp):
    store = Store(os.path.join(tmp, "runtime.db"))
    adapters = {"review_system": ReviewSystemAdapter(os.path.join(tmp, "rs.db")),
                "ats": AtsAdapter(os.path.join(tmp, "ats.db"))}
    step = next(s for s in cfg["steps"] if s["type"] == "model")
    runner = Runner(cfg, store, adapters, ReplayModel(step["responses"]),
                    printer=lambda *a, **k: None)
    return runner, store, adapters


class TestDerivedKey(unittest.TestCase):
    def test_format(self):
        self.assertEqual(
            derive_key("moza", "song_screening", "run_2026_03_14", "create_task", "song_042"),
            "moza:song_screening:run_2026_03_14:create_task:song_042")

    def test_same_run_item_stable_diff_run_distinct(self):
        a = derive_key("moza", "song_screening", "run_100", "create_task", "song_042")
        b = derive_key("moza", "song_screening", "run_100", "create_task", "song_042")
        c = derive_key("moza", "song_screening", "run_101", "create_task", "song_042")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_two_actions_same_item_get_distinct_keys(self):
        # H1: a workflow with two external steps on one item must not collide
        create = derive_key("v", "w", "run_1", "create_task", "item_1")
        notify = derive_key("v", "w", "run_1", "notify", "item_1")
        self.assertNotEqual(create, notify)

    def test_run_id_is_required_not_generated(self):
        # the platform must refuse to invent identity: a generated run_id would
        # make each unattended retry a new run and duplicate the action
        cfg = load_config(MOZA)
        tmp = tempfile.mkdtemp()
        runner, store, adapters = build(cfg, tmp)
        with self.assertRaises(ValueError):
            runner.run("")


class TestNoDuplicateAcrossRepeats(unittest.TestCase):
    def test_same_run_three_times_is_one_task_each(self):
        cfg = load_config(MOZA)
        tmp = tempfile.mkdtemp()
        for _ in range(3):
            runner, store, adapters = build(cfg, tmp)  # reopen state each time
            runner.run("run_x")
        committed = [a for a in store.list_actions()
                     if a["action_status"] == "committed"]
        self.assertEqual(len(committed), 5)  # includes, no duplicates
        n = adapters["review_system"].conn.execute(
            "SELECT COUNT(*) c FROM records").fetchone()["c"]
        self.assertEqual(n, 5)

    def test_two_runs_same_items_two_tasks(self):
        cfg = load_config(MOZA)
        tmp = tempfile.mkdtemp()
        r1, store, adapters = build(cfg, tmp)
        r1.run("run_100")
        r2 = Runner(cfg, store, adapters,
                    ReplayModel(next(s for s in cfg["steps"]
                                     if s["type"] == "model")["responses"]),
                    printer=lambda *a, **k: None)
        r2.run("run_101")
        k100 = "moza:song_screening:run_100:create_task:song_041"
        k101 = "moza:song_screening:run_101:create_task:song_041"
        t100 = adapters["review_system"].find_by_key(k100)
        t101 = adapters["review_system"].find_by_key(k101)
        self.assertIsNotNone(t100)
        self.assertIsNotNone(t101)
        self.assertNotEqual(t100, t101)


class TestCompletedRunReplay(unittest.TestCase):
    def test_rerunning_a_completed_run_is_a_noop(self):
        # whole-run replay safety: re-running a completed run_id creates nothing
        cfg = load_config(MOZA)
        tmp = tempfile.mkdtemp()
        cfg["steps"][0]["source"] = os.path.abspath(
            "workflows/moza_song_screening/fixtures/songs_crash.json")  # completes
        r1, store, adapters = build(cfg, tmp)
        self.assertEqual(r1.run("run_c"), "completed")
        n_after_first = adapters["review_system"].conn.execute(
            "SELECT COUNT(*) c FROM records").fetchone()["c"]
        self.assertEqual(n_after_first, 1)
        # reopen state, run the same id again -> early return, no new action
        r2, store2, adapters2 = build(cfg, tmp)
        self.assertEqual(r2.run("run_c"), "completed")
        self.assertEqual(adapters2["review_system"].conn.execute(
            "SELECT COUNT(*) c FROM records").fetchone()["c"], 1)


class TestRunnerPreventsDuplicateNotMock(unittest.TestCase):
    """Guards against the mutation the reviewer used: with reconcile removed,
    the old tests still passed because the mock deduped by its own PK. This
    test uses a non-idempotent target, so only the runner's reconcile logic
    can prevent the duplicate."""

    def test_crash_b_retry_creates_exactly_one_against_non_idempotent_target(self):
        tmp = tempfile.mkdtemp()
        adapter = PlainInsertAdapter(os.path.join(tmp, "ext.db"))
        store = Store(os.path.join(tmp, "runtime.db"))
        step = {"name": "create_task", "approval": "auto", "idempotent": True}
        key = derive_key("moza", "song_screening", "run_b", "create_task", "song_041")
        # simulate crash-B: the action already executed once; the ledger was
        # left at `intent` because the commit never happened.
        adapter.execute(step, key)
        self.assertEqual(adapter.count(), 1)
        store.create_run("run_b", "moza", "song_screening")
        store.create_action(key, "run_b", "approved", "intent")
        # retry: the runner must reconcile (YES) and NOT execute again
        perform_external(store, adapter, "run_b", "song_041", key, step,
                         approval_override=None, crash_at=None,
                         printer=lambda *a, **k: None)
        self.assertEqual(adapter.count(), 1)  # would be 2 if the runner blindly re-executed
        self.assertEqual(store.get_action(key)["action_status"], "committed")


if __name__ == "__main__":
    unittest.main()
