"""The scenario the brief names, tested across a REAL process boundary.

The crash is os._exit(137), so it can only be exercised by running the CLI
as a subprocess -- which is also the point: state must survive a genuine
process death, not linger in memory.
"""
import os
import subprocess
import sys
import tempfile
import unittest

from core.state import Store
from adapters.external import ReviewSystemAdapter

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MOZA = "workflows/moza_song_screening/config.json"
# single-include-song source: the crash story is about ONE task existing
# exactly once, so we scope the run to one song and assert "1 task, never 2".
CRASH_SRC = "workflows/moza_song_screening/fixtures/songs_crash.json"
KEY = "moza:song_screening:{run}:song_041"


def run_cli(state_dir, run_id, *extra):
    return subprocess.run(
        [sys.executable, "cli.py", "run", "--config", MOZA,
         "--source", CRASH_SRC, "--state-dir", state_dir, "--run-id", run_id, *extra],
        cwd=REPO, capture_output=True, text=True,
    )


def count_records(state_dir):
    a = ReviewSystemAdapter(os.path.join(state_dir, "review_system.db"))
    return a.conn.execute("SELECT COUNT(*) c FROM records").fetchone()["c"]


class TestCrashA(unittest.TestCase):
    def test_crash_before_call_then_retry_creates_once(self):
        d = tempfile.mkdtemp()
        p1 = run_cli(d, "run_a", "--crash-at", "A")
        self.assertNotEqual(p1.returncode, 0)   # os._exit(137)
        self.assertEqual(count_records(d), 0)   # call never happened
        self.assertEqual(run_cli(d, "run_a").returncode, 0)
        self.assertEqual(count_records(d), 1)   # created on retry
        st = Store(os.path.join(d, "runtime.db"))
        self.assertEqual(st.get_action(KEY.format(run="run_a"))["action_status"],
                         "committed")


class TestCrashB(unittest.TestCase):
    def test_crash_after_call_then_retry_does_not_duplicate(self):
        d = tempfile.mkdtemp()
        p1 = run_cli(d, "run_b", "--crash-at", "B")
        self.assertNotEqual(p1.returncode, 0)
        self.assertEqual(count_records(d), 1)   # the action DID happen
        for _ in range(3):                      # retry three times
            self.assertEqual(run_cli(d, "run_b").returncode, 0)
        self.assertEqual(count_records(d), 1)   # reconcile, never a second


class TestCrashBExternalDown(unittest.TestCase):
    def test_down_retry_stays_intent_and_stops(self):
        d = tempfile.mkdtemp()
        run_cli(d, "run_b", "--crash-at", "B")
        run_cli(d, "run_b", "--external", "down")
        st = Store(os.path.join(d, "runtime.db"))
        self.assertEqual(st.get_action(KEY.format(run="run_b"))["action_status"],
                         "intent")
        self.assertEqual(st.get_run("run_b")["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
