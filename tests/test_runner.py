"""Execution behaviour: the runner dispatches by step type, the model
verdict changes behaviour, deterministic lookups gate/stop, a missing
identifier stops at fetch, and the SAME runner executes a second workflow
(Helios) with a different step order, identifier, target and approval."""
import os
import tempfile
import unittest

from core.runner import load_config, Runner
from core.state import Store
from adapters.external import ReviewSystemAdapter, AtsAdapter
from adapters.model import ReplayModel

MOZA = "workflows/moza_song_screening/config.json"
HELIOS = "workflows/helios_recruiting_screening/config.json"


def build(cfg, tmp, **kw):
    store = Store(os.path.join(tmp, "runtime.db"))
    adapters = {
        "review_system": ReviewSystemAdapter(os.path.join(tmp, "rs.db"),
                                             down=kw.pop("down", False)),
        "ats": AtsAdapter(os.path.join(tmp, "ats.db")),
    }
    model_step = next((s for s in cfg["steps"] if s["type"] == "model"), None)
    model = ReplayModel(model_step["responses"]) if model_step else None
    runner = Runner(cfg, store, adapters, model,
                    printer=lambda *a, **k: None, **kw)
    return runner, store, adapters


class TestMozaExecution(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(MOZA)
        self.tmp = tempfile.mkdtemp()

    def test_only_includes_create_tasks(self):
        runner, store, adapters = build(self.cfg, self.tmp)
        status = runner.run("run_demo")
        self.assertEqual(status, "stopped")  # trailing unknown-rights song
        committed = [a for a in store.list_actions()
                     if a["action_status"] == "committed"]
        # includes 041,044,045,047,049; excludes/needs_review/not_licensed => none
        self.assertEqual(len(committed), 5)

    def test_exclude_and_needs_review_produce_no_action(self):
        runner, store, adapters = build(self.cfg, self.tmp)
        runner.run("run_demo")
        keys = {a["idempotency_key"] for a in store.list_actions()}
        self.assertNotIn("moza:song_screening:run_demo:song_042", keys)  # exclude
        self.assertNotIn("moza:song_screening:run_demo:song_050", keys)  # needs_review

    def test_unknown_rights_stops_run(self):
        runner, store, adapters = build(self.cfg, self.tmp)
        runner.run("run_demo")
        run = store.get_run("run_demo")
        self.assertEqual(run["status"], "stopped")
        self.assertIn("unknown", run["stopped_reason"])

    def test_not_licensed_is_skipped_not_stopped(self):
        runner, store, adapters = build(self.cfg, self.tmp)
        runner.run("run_demo")
        step = store.get_step("run_demo", "rights_check", "song_048")
        self.assertEqual(step["result"], "skipped")

    def test_missing_item_id_stops_at_fetch(self):
        cfg = load_config(MOZA)
        cfg["steps"][0]["source"] = os.path.abspath(
            "workflows/moza_song_screening/fixtures/songs_missing_id.json")
        runner, store, adapters = build(cfg, self.tmp)
        status = runner.run("run_bad")
        self.assertEqual(status, "stopped")
        self.assertIn("position 1", store.get_run("run_bad")["stopped_reason"])
        self.assertEqual(len(store.list_actions()), 0)

    def test_approval_required_waits(self):
        runner, store, adapters = build(self.cfg, self.tmp, approval_override="required")
        runner.run("run_demo")
        actions = store.list_actions()
        self.assertTrue(actions)
        self.assertTrue(all(a["action_status"] != "committed" for a in actions))
        self.assertTrue(all(a["approval_status"] == "pending" for a in actions))


class TestHeliosOnSameRunner(unittest.TestCase):
    """The reuse proof: a different VT runs on the identical runner."""

    def test_helios_runs_with_different_shape(self):
        cfg = load_config(HELIOS)
        tmp = tempfile.mkdtemp()
        # override approval so the advancing candidate's note actually executes
        runner, store, adapters = build(cfg, tmp, approval_override="auto")
        status = runner.run("run_h1")
        self.assertEqual(status, "completed")
        # cand_01 advances -> routed -> note created in the ATS (not review system)
        self.assertEqual(store.get_step("run_h1", "route", "cand_01")["result"], "ok")
        note = adapters["ats"].find_by_key("helios:recruiting_screening:run_h1:cand_01")
        self.assertTrue(note.startswith("N-"))
        # cand_02 reject, cand_03 needs_review -> no note
        self.assertIsNone(
            adapters["ats"].find_by_key("helios:recruiting_screening:run_h1:cand_02"))
        # review_system adapter was never touched by Helios
        self.assertEqual(
            adapters["review_system"].conn.execute(
                "SELECT COUNT(*) c FROM records").fetchone()["c"], 0)


class TestConfigErrors(unittest.TestCase):
    def _minimal(self, external_step):
        return {
            "vt": "x", "workflow": "w", "item_id_field": "id",
            "steps": [
                {"name": "fetch", "type": "deterministic", "scope": "run",
                 "source": os.path.abspath(
                     "workflows/moza_song_screening/fixtures/songs.json")},
                external_step,
            ],
        }

    def test_unknown_target_stops_readably(self):
        cfg = self._minimal({"name": "act", "type": "external", "target": "nope",
                             "idempotent": True})
        cfg["item_id_field"] = "song_id"
        tmp = tempfile.mkdtemp()
        runner, store, _ = build(cfg, tmp)
        self.assertEqual(runner.run("r"), "stopped")
        self.assertIn("no adapter", store.get_run("r")["stopped_reason"])

    def test_unknown_step_type_stops_readably(self):
        cfg = self._minimal({"name": "act", "type": "quantum"})
        cfg["item_id_field"] = "song_id"
        tmp = tempfile.mkdtemp()
        runner, store, _ = build(cfg, tmp)
        self.assertEqual(runner.run("r"), "stopped")
        self.assertIn("unknown step type", store.get_run("r")["stopped_reason"])

    def test_external_without_idempotency_contract_is_refused(self):
        # an external step with no `idempotent: true` must be refused before
        # it can act -- the platform enforces the contract, not just trusts it
        cfg = self._minimal({"name": "act", "type": "external", "target": "review_system"})
        cfg["item_id_field"] = "song_id"
        tmp = tempfile.mkdtemp()
        runner, store, adapters = build(cfg, tmp)
        self.assertEqual(runner.run("r"), "stopped")
        self.assertIn("idempotency contract", store.get_run("r")["stopped_reason"])
        # nothing was created
        self.assertEqual(adapters["review_system"].conn.execute(
            "SELECT COUNT(*) c FROM records").fetchone()["c"], 0)


class TestModelFailureHalts(unittest.TestCase):
    """A malformed model verdict is a controlled halt, not an uncaught crash
    that leaves the run stuck at 'running'."""

    def test_out_of_contract_verdict_stops_run_cleanly(self):
        import json
        cfg = load_config(MOZA)
        tmp = tempfile.mkdtemp()
        cfg["steps"][0]["source"] = os.path.abspath(
            "workflows/moza_song_screening/fixtures/songs_crash.json")  # one song
        bad = os.path.join(tmp, "bad_responses.json")
        with open(bad, "w") as f:
            json.dump({"song_041": {"verdict": "banana", "reason": "not an outcome"}}, f)
        for s in cfg["steps"]:
            if s["type"] == "model":
                s["responses"] = bad
        runner, store, adapters = build(cfg, tmp)
        status = runner.run("run_bad_model")   # must NOT raise
        self.assertEqual(status, "stopped")
        run = store.get_run("run_bad_model")
        self.assertEqual(run["status"], "stopped")          # not stuck at "running"
        self.assertIn("model decision failed", run["stopped_reason"])
        self.assertEqual(len(store.list_actions()), 0)      # no external action


if __name__ == "__main__":
    unittest.main()
