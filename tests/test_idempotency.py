"""The property the crash scenario is really about: a derived key that is
stable across retries of a run and distinct across separate runs."""
import os
import tempfile
import unittest

from core.actions import derive_key
from core.runner import load_config, Runner
from core.state import Store
from adapters.external import ReviewSystemAdapter, AtsAdapter
from adapters.model import ReplayModel

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
            derive_key("moza", "song_screening", "run_2026_03_14", "song_042"),
            "moza:song_screening:run_2026_03_14:song_042")

    def test_same_run_item_stable_diff_run_distinct(self):
        a = derive_key("moza", "song_screening", "run_100", "song_042")
        b = derive_key("moza", "song_screening", "run_100", "song_042")
        c = derive_key("moza", "song_screening", "run_101", "song_042")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


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
        k100 = "moza:song_screening:run_100:song_041"
        k101 = "moza:song_screening:run_101:song_041"
        t100 = adapters["review_system"].find_by_key(k100)
        t101 = adapters["review_system"].find_by_key(k101)
        self.assertIsNotNone(t100)
        self.assertIsNotNone(t101)
        self.assertNotEqual(t100, t101)


if __name__ == "__main__":
    unittest.main()
