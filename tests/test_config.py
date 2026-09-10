import unittest
from vt_runtime.config import load_config


class TestConfig(unittest.TestCase):
    def test_loads_moza(self):
        cfg = load_config("configs/moza_song_screening.json")
        self.assertEqual(cfg["vt"], "moza")
        self.assertEqual(cfg["item_id_field"], "song_id")
        names = [s["name"] for s in cfg["steps"]]
        self.assertEqual(names, ["fetch", "rights_check", "judge", "create_task"])

    def test_loads_helios_shape(self):
        cfg = load_config("configs/helios_recruiting_screening.json")
        self.assertEqual(cfg["item_id_field"], "candidate_id")
        # helios judges BEFORE routing; different order proves reusability
        names = [s["name"] for s in cfg["steps"]]
        self.assertEqual(names, ["fetch", "judge", "route", "create_note"])


if __name__ == "__main__":
    unittest.main()
