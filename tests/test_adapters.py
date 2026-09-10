"""The platform boundary: external adapters, the model adapter, and the
durable store they persist through."""
import json
import os
import tempfile
import unittest

from adapters.external import (
    ReviewSystemAdapter, AtsAdapter, ExternalUnavailable,
    RECONCILE_YES, RECONCILE_NO, RECONCILE_UNKNOWN,
)
from adapters.model import ReplayModel, LiveModel
from core.state import Store


class TestExternalAdapters(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_execute_then_reconcile_finds(self):
        a = ReviewSystemAdapter(os.path.join(self.dir, "rs.db"))
        tid = a.execute({"operation": "create_task"}, "k1")
        self.assertTrue(tid.startswith("T-"))
        self.assertEqual(a.reconcile("k1"), (RECONCILE_YES, tid))

    def test_reconcile_no_when_absent(self):
        a = ReviewSystemAdapter(os.path.join(self.dir, "rs.db"))
        self.assertEqual(a.reconcile("missing"), (RECONCILE_NO, None))

    def test_execute_idempotent_at_target(self):
        a = ReviewSystemAdapter(os.path.join(self.dir, "rs.db"))
        self.assertEqual(a.execute({}, "k1"), a.execute({}, "k1"))

    def test_down_execute_raises_and_reconcile_cannot_answer(self):
        path = os.path.join(self.dir, "rs.db")
        ReviewSystemAdapter(path).execute({}, "k1")
        down = ReviewSystemAdapter(path, down=True)
        with self.assertRaises(ExternalUnavailable):
            down.execute({}, "k2")
        self.assertEqual(down.reconcile("k1"), (RECONCILE_UNKNOWN, None))

    def test_ats_has_its_own_id_space(self):
        a = AtsAdapter(os.path.join(self.dir, "ats.db"))
        self.assertTrue(a.execute({}, "k1").startswith("N-"))


class TestModelAdapter(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "r.json")
        with open(self.path, "w") as f:
            json.dump({"a": {"verdict": "include", "reason": "ok"},
                       "b": {"verdict": "banana", "reason": "x"}}, f)

    def test_replays_recorded_verdict(self):
        v, r = ReplayModel(self.path).judge("a", {}, "p", ["include", "exclude"])
        self.assertEqual((v, r), ("include", "ok"))

    def test_missing_item_raises(self):
        with self.assertRaises(KeyError):
            ReplayModel(self.path).judge("missing", {}, "p", ["include"])

    def test_verdict_outside_outcomes_raises(self):
        with self.assertRaises(ValueError):
            ReplayModel(self.path).judge("b", {}, "p", ["include", "exclude"])

    def test_live_model_is_a_stub(self):
        with self.assertRaises(NotImplementedError):
            LiveModel().judge("a", {}, "p", ["include"])


class TestStorePersistence(unittest.TestCase):
    def test_state_survives_reopen(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "s.db")
        s1 = Store(path)
        s1.create_run("run_1", "moza", "song_screening")
        s1.create_action("k", "run_1", "approved", "intent")
        s1.close()
        s2 = Store(path)
        self.assertEqual(s2.get_action("k")["action_status"], "intent")
        s2.close()

    def test_recorded_step_is_idempotent(self):
        d = tempfile.mkdtemp()
        s = Store(os.path.join(d, "s.db"))
        s.create_run("r", "moza", "song_screening")
        s.record_step("r", "rights_check", "song_041", "ok", "first")
        s.record_step("r", "rights_check", "song_041", "ok", "second")
        rows = [x for x in s.list_steps("r") if x["item_id"] == "song_041"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["detail"], "first")


if __name__ == "__main__":
    unittest.main()
