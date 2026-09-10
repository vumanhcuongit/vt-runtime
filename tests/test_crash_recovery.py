import os
import subprocess
import sys
import tempfile
import unittest
from vt_runtime.state import Store
from vt_runtime.adapter import ReviewSystemAdapter

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MOZA = "configs/moza_song_screening.json"


def run_cli(state_dir, *extra):
    env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src"))
    return subprocess.run(
        [sys.executable, "-m", "vt_runtime", "run", "--config", MOZA,
         "--state-dir", state_dir, *extra],
        cwd=REPO, env=env, capture_output=True, text=True,
    )


def count_tasks(state_dir):
    a = ReviewSystemAdapter(os.path.join(state_dir, "review_system.db"))
    return a.conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]


class TestCrashA(unittest.TestCase):
    def test_crash_a_then_retry_creates_once(self):
        d = tempfile.mkdtemp()
        # crash A leaves 'intent', no task in external system
        p1 = run_cli(d, "--run-id", "run_a", "--crash-at", "A")
        self.assertNotEqual(p1.returncode, 0)  # os._exit(137)
        # retry: reconcile says 'no' -> create now, exactly one
        p2 = run_cli(d, "--run-id", "run_a")
        self.assertEqual(p2.returncode, 0)
        self.assertEqual(count_tasks(d), 1)
        key = "moza:song_screening:run_a:song_041"
        st = Store(os.path.join(d, "runtime.db"))
        self.assertEqual(st.get_action(key)["action_status"], "committed")


class TestCrashB(unittest.TestCase):
    def test_crash_b_then_retry_does_not_duplicate(self):
        d = tempfile.mkdtemp()
        # crash B: task exists in external system, ledger still 'intent'
        p1 = run_cli(d, "--run-id", "run_b", "--crash-at", "B")
        self.assertNotEqual(p1.returncode, 0)
        self.assertEqual(count_tasks(d), 1)  # the task WAS created
        # retry three times: reconcile finds it, never creates a second
        for _ in range(3):
            self.assertEqual(run_cli(d, "--run-id", "run_b").returncode, 0)
        self.assertEqual(count_tasks(d), 1)


class TestCrashBExternalDown(unittest.TestCase):
    def test_down_retry_stays_intent_and_stops(self):
        d = tempfile.mkdtemp()
        run_cli(d, "--run-id", "run_b", "--crash-at", "B")
        # retry with external down: cannot reconcile -> stays intent, stops
        run_cli(d, "--run-id", "run_b", "--external", "down")
        key = "moza:song_screening:run_b:song_041"
        st = Store(os.path.join(d, "runtime.db"))
        self.assertEqual(st.get_action(key)["action_status"], "intent")
        self.assertEqual(st.get_run("run_b")["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
