import os
import tempfile
import unittest
from vt_runtime.state import Store
from vt_runtime.inspect import render


class TestInspect(unittest.TestCase):
    def test_render_contains_run_steps_and_actions(self):
        d = tempfile.mkdtemp()
        store = Store(os.path.join(d, "t.db"))
        store.create_run("run_x", "moza", "song_screening")
        store.record_step("run_x", "fetch", None, "ok", "10 items")
        store.record_step("run_x", "create_task", "song_041", "committed", "T-991")
        store.create_action("moza:song_screening:run_x:song_041", "run_x",
                            "approved", "committed")
        store.set_action_status("moza:song_screening:run_x:song_041",
                                "committed", "T-991")
        out = render(store)
        self.assertIn("RUN run_x", out)
        self.assertIn("moza/song_screening", out)
        self.assertIn("fetch", out)
        self.assertIn("EXTERNAL ACTIONS", out)
        self.assertIn("moza:song_screening:run_x:song_041", out)
        self.assertIn("T-991", out)


if __name__ == "__main__":
    unittest.main()
