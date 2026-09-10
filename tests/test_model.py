import json
import os
import tempfile
import unittest
from vt_runtime.model import ReplayModel, LiveModel


class TestReplayModel(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "responses.json")
        with open(self.path, "w") as f:
            json.dump({
                "song_041": {"verdict": "include", "reason": "clear rhythm"},
                "song_099": {"verdict": "banana", "reason": "bad"},
            }, f)

    def test_returns_recorded_verdict(self):
        m = ReplayModel(self.path)
        v, r = m.judge("song_041", {"song_id": "song_041"}, "prompt",
                       ["include", "exclude", "needs_review"])
        self.assertEqual(v, "include")
        self.assertEqual(r, "clear rhythm")

    def test_missing_item_raises(self):
        m = ReplayModel(self.path)
        with self.assertRaises(KeyError):
            m.judge("song_missing", {}, "p", ["include"])

    def test_verdict_outside_outcomes_raises(self):
        m = ReplayModel(self.path)
        with self.assertRaises(ValueError):
            m.judge("song_099", {}, "p", ["include", "exclude", "needs_review"])


class TestLiveModel(unittest.TestCase):
    def test_stub_raises(self):
        with self.assertRaises(NotImplementedError):
            LiveModel().judge("song_041", {}, "p", ["include"])


if __name__ == "__main__":
    unittest.main()
