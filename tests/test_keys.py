import unittest
from vt_runtime.keys import build_key


class TestBuildKey(unittest.TestCase):
    def test_format_matches_spec(self):
        self.assertEqual(
            build_key("moza", "song_screening", "run_2026_03_14", "song_042"),
            "moza:song_screening:run_2026_03_14:song_042",
        )

    def test_same_run_same_item_is_stable(self):
        a = build_key("moza", "song_screening", "run_100", "song_042")
        b = build_key("moza", "song_screening", "run_100", "song_042")
        self.assertEqual(a, b)

    def test_different_run_same_item_differs(self):
        a = build_key("moza", "song_screening", "run_100", "song_042")
        b = build_key("moza", "song_screening", "run_101", "song_042")
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
