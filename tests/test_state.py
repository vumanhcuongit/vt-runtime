import os
import tempfile
import unittest
from vt_runtime.state import Store


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "t.db"))

    def tearDown(self):
        self.store.close()

    def test_run_lifecycle(self):
        self.store.create_run("run_1", "moza", "song_screening")
        run = self.store.get_run("run_1")
        self.assertEqual(run["status"], "running")
        self.store.set_run_status("run_1", "completed")
        self.assertEqual(self.store.get_run("run_1")["status"], "completed")

    def test_step_is_idempotent_on_unique(self):
        self.store.create_run("run_1", "moza", "song_screening")
        self.store.record_step("run_1", "rights_check", "song_041", "ok", "licensed")
        # second write for same (run, step, item) is ignored, not duplicated
        self.store.record_step("run_1", "rights_check", "song_041", "ok", "licensed again")
        rows = [s for s in self.store.list_steps("run_1")
                if s["step_name"] == "rights_check" and s["item_id"] == "song_041"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["detail"], "licensed")  # first write wins

    def test_run_level_step_null_item(self):
        self.store.create_run("run_1", "moza", "song_screening")
        self.store.record_step("run_1", "fetch", None, "ok", "10 songs")
        self.assertIsNotNone(self.store.get_step("run_1", "fetch", None))

    def test_action_state_machine(self):
        self.store.create_run("run_1", "moza", "song_screening")
        key = "moza:song_screening:run_1:song_041"
        self.store.create_action(key, "run_1", "approved", "none")
        self.assertEqual(self.store.get_action(key)["action_status"], "none")
        self.store.set_action_status(key, "intent")
        self.assertEqual(self.store.get_action(key)["action_status"], "intent")
        self.store.set_action_status(key, "committed", result="T-991")
        got = self.store.get_action(key)
        self.assertEqual(got["action_status"], "committed")
        self.assertEqual(got["result"], "T-991")

    def test_persists_across_reopen(self):
        path = os.path.join(self.dir, "persist.db")
        s1 = Store(path)
        s1.create_run("run_1", "moza", "song_screening")
        s1.create_action("k", "run_1", "approved", "intent")
        s1.close()
        s2 = Store(path)
        self.assertEqual(s2.get_action("k")["action_status"], "intent")
        s2.close()


if __name__ == "__main__":
    unittest.main()
