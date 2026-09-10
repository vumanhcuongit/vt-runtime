import json
import os
import tempfile
import unittest
from vt_runtime.state import Store
from vt_runtime.adapter import ReviewSystemAdapter
from vt_runtime.model import ReplayModel
from vt_runtime.config import load_config
from vt_runtime.runner import Runner


def _write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


class RunnerHarness(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.responses = os.path.join(self.dir, "responses.json")
        _write(self.responses, {
            "song_041": {"verdict": "include", "reason": "clear rhythm, simple melody"},
            "song_042": {"verdict": "exclude", "reason": "tempo too fast for beginners"},
            "song_044": {"verdict": "needs_review", "reason": "borderline tempo"},
            "song_045": {"verdict": "include", "reason": "steady beat"},
            "song_046": {"verdict": "exclude", "reason": "too fast"},
            "song_047": {"verdict": "include", "reason": "simple waltz"},
            "song_049": {"verdict": "include", "reason": "slow and gentle"},
            "song_050": {"verdict": "include", "reason": "steady tempo"},
        })
        self.cfg = load_config("configs/moza_song_screening.json")

    def _runner(self, **kw):
        store = Store(os.path.join(self.dir, "runtime.db"))
        adapter = ReviewSystemAdapter(os.path.join(self.dir, "review.db"),
                                      down=kw.pop("down", False))
        model = ReplayModel(self.responses)
        return (Runner(self.cfg, store, adapter, model,
                       printer=lambda *a, **k: None, **kw), store, adapter)


class TestNormalRun(RunnerHarness):
    def test_include_creates_task_exclude_and_needs_review_do_not(self):
        runner, store, adapter = self._runner()
        status = runner.run("run_demo")
        self.assertEqual(status, "stopped")  # stops at trailing unknown song_043
        actions = store.list_actions()
        committed = [a for a in actions if a["action_status"] == "committed"]
        # song_041, 045, 047, 049, 050 are include+licensed = 5 tasks;
        # song_042/046 exclude, 044 needs_review, 048 not_licensed => no task
        self.assertEqual(len(committed), 5)
        # exactly one task per include, no duplicates
        keys = {a["idempotency_key"] for a in committed}
        self.assertEqual(len(keys), 5)

    def test_unknown_rights_stops_run(self):
        runner, store, adapter = self._runner()
        runner.run("run_demo")
        run = store.get_run("run_demo")
        self.assertEqual(run["status"], "stopped")
        self.assertIn("unknown", (run["stopped_reason"] or ""))


class TestApproval(RunnerHarness):
    def test_required_approval_waits(self):
        runner, store, adapter = self._runner(approval_override="required")
        runner.run("run_demo")
        actions = store.list_actions()
        # nothing committed; includes sit pending/none
        self.assertTrue(all(a["action_status"] != "committed" for a in actions))
        self.assertTrue(any(a["approval_status"] == "pending" for a in actions))
        # external system has zero tasks
        self.assertIsNone(adapter.find_task_by_key(
            "moza:song_screening:run_demo:song_041"))


class TestKeyStability(RunnerHarness):
    def test_same_run_repeated_yields_one_task(self):
        # run three times with same run_id -> exactly one task per include
        for _ in range(3):
            runner, store, adapter = self._runner()
            runner.run("run_demo")
        committed = [a for a in store.list_actions() if a["action_status"] == "committed"]
        self.assertEqual(len(committed), 5)

    def test_different_runs_same_item_two_tasks(self):
        runner1, store, adapter = self._runner()
        runner1.run("run_100")
        # reuse same dbs by building runner on same dir
        runner2 = Runner(self.cfg, store, adapter, ReplayModel(self.responses),
                         printer=lambda *a, **k: None)
        runner2.run("run_101")
        k100 = "moza:song_screening:run_100:song_041"
        k101 = "moza:song_screening:run_101:song_041"
        self.assertIsNotNone(adapter.find_task_by_key(k100))
        self.assertIsNotNone(adapter.find_task_by_key(k101))
        self.assertNotEqual(adapter.find_task_by_key(k100),
                            adapter.find_task_by_key(k101))


if __name__ == "__main__":
    unittest.main()
