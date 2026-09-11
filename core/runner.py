"""The reusable workflow runner.

Executes a workflow's steps in order, dispatching by step **type**
(deterministic / model / external) and scope (run-level fetch vs
item-level) -- never by step name. A different workflow is a different
config file, not different code, so this module contains no domain words.

Recording that a run finished is done HERE, by the platform -- not by a
config step. If completion were a workflow step, the workflow would own
tracking its own completion, which is exactly the responsibility this
runner exists to take over. That is why no config has a completion step.
"""
import json
import os

from core.observability import format_row
from core.platform import Platform, RunStopped

# fields in a step whose values are file paths, resolved relative to the
# config file so each workflow folder is self-contained
_PATH_FIELDS = ("source", "lookup", "rules", "prompt", "responses")


def load_config(path: str) -> dict:
    with open(path) as f:
        cfg = json.load(f)
    base = os.path.dirname(os.path.abspath(path))
    for step in cfg["steps"]:
        for field in _PATH_FIELDS:
            if field in step:
                step[field] = os.path.join(base, step[field])
    return cfg


class Runner:
    def __init__(self, config, store, adapters, model, *,
                 crash_at=None, approval_override=None, printer=print):
        self.cfg = config
        self.store = store
        self.model = model
        self.crash_at = crash_at
        self.approval_override = approval_override
        self.print = printer
        # the reusable capability; the runner is just a caller of it
        self.platform = Platform(store, adapters, printer=printer)
        self.vt = config["vt"]
        self.workflow = config["workflow"]
        self.item_id_field = config["item_id_field"]
        self.steps = config["steps"]

    def run(self, run_id: str) -> str:
        # Identity comes from the trigger instance; the platform never
        # generates it. A generated (e.g. timestamped) id would silently turn
        # each unattended retry into a NEW run -- new key, duplicate action --
        # which is the exact failure this whole design exists to prevent.
        if not run_id:
            raise ValueError(
                "run_id is required: it must be the trigger-instance id; "
                "the platform does not generate identity")
        existing = self.store.get_run(run_id)
        if existing and existing["status"] == "completed":
            self.print(f"RUN {run_id} already completed; nothing to do.")
            return "completed"
        self.store.create_run(run_id, self.vt, self.workflow)

        fetch_step = self._fetch_step()
        item_steps = [s for s in self.steps if s is not fetch_step]
        try:
            self._validate_steps()
            items = self._fetch(run_id, fetch_step)
            for item in items:
                item_id = item[self.item_id_field]
                for step in item_steps:
                    if not self._run_step(run_id, step, item, item_id):
                        break  # this item is done; move to the next
        except RunStopped as stop:
            self.store.set_run_status(run_id, "stopped", str(stop))
            self.print(f"RUN {run_id} STOPPED: {stop}")
            return "stopped"

        self.store.set_run_status(run_id, "completed")
        self.print(f"RUN {run_id} completed.")
        return "completed"

    def _fetch_step(self):
        # The run-level step is the one declaring scope: "run" (honouring the
        # config field rather than assuming steps[0]); fall back to the first
        # step if none is declared.
        for step in self.steps:
            if step.get("scope") == "run":
                return step
        return self.steps[0]

    def _validate_steps(self):
        # An external action must carry an idempotency contract. A real
        # platform should refuse to act on one that doesn't, rather than
        # taking the config's word for it -- so the runner enforces it.
        for step in self.steps:
            if step["type"] == "external" and not step.get("idempotent"):
                raise RunStopped(
                    f"external step '{step['name']}' declares no idempotency "
                    f"contract (idempotent: true); refusing to perform it")

    # ---- run-level fetch ----
    def _fetch(self, run_id, step):
        with open(step["source"]) as f:
            items = json.load(f)
        # Every item needs a stable identifier for its idempotency key. If
        # one is missing we STOP at fetch -- never fall back to list index,
        # which breaks silently the moment the source reorders.
        for pos, item in enumerate(items):
            if self.item_id_field not in item:
                reason = (f"item at position {pos} is missing required field "
                          f"'{self.item_id_field}'; stopped at fetch "
                          f"(no list-index fallback)")
                self.store.record_step(run_id, step["name"], None, "stopped", reason)
                raise RunStopped(reason)
        if not self.store.get_step(run_id, step["name"], None):
            self.store.record_step(run_id, step["name"], None, "ok", f"{len(items)} items")
            self.print(format_row(step["name"], None, "ok", f"{len(items)} items"))
        return items

    # ---- item-level dispatch by type ----
    def _run_step(self, run_id, step, item, item_id) -> bool:
        """Return True to continue to the next step, False to skip the rest
        of this item's steps."""
        t = step["type"]
        if t == "deterministic":
            return self._deterministic(run_id, step, item, item_id)
        if t == "model":
            return self._model(run_id, step, item, item_id)
        if t == "external":
            # the workflow hands the intended business action to the platform;
            # the platform owns the write-once + approval + reconcile logic
            self.platform.write(
                vt=self.vt, workflow=self.workflow, run_id=run_id,
                item_id=item_id, operation=step["name"], target=step["target"],
                payload=item,
                approval=self.approval_override or step.get("approval", "auto"),
                crash_at=self.crash_at)
            return True
        raise RunStopped(f"unknown step type '{t}' in step '{step['name']}'")

    # ---- deterministic: lookup (gate) or map (annotate) ----
    def _deterministic(self, run_id, step, item, item_id) -> bool:
        prior = self.store.get_step(run_id, step["name"], item_id)
        if prior:
            if prior["result"] == "unknown" and step.get("on_unknown") == "stop":
                raise RunStopped(prior["detail"])
            return prior["result"] == "ok"
        if "lookup" in step:
            return self._lookup(run_id, step, item_id)
        if "rules" in step:
            return self._map(run_id, step, item, item_id)
        self.store.record_step(run_id, step["name"], item_id, "ok", "")
        return True

    def _lookup(self, run_id, step, item_id) -> bool:
        with open(step["lookup"]) as f:
            table = json.load(f)
        value = table.get(item_id)
        if value in step.get("pass_values", []):
            self.store.record_step(run_id, step["name"], item_id, "ok", str(value))
            self.print(format_row(step["name"], item_id, "ok", str(value)))
            return True
        if value is None or value in step.get("unknown_values", []):
            if step.get("on_unknown") == "stop":
                reason = (f"{step['name']} unknown for {item_id}: "
                          f"registry did not answer -> run stopped")
                self.store.record_step(run_id, step["name"], item_id, "unknown", reason)
                self.print(format_row(step["name"], item_id, "unknown", reason))
                raise RunStopped(reason)
            self.store.record_step(run_id, step["name"], item_id, "flagged",
                                   f"unknown: {value}")
            self.print(format_row(step["name"], item_id, "flagged", f"unknown: {value}"))
            return False
        self.store.record_step(run_id, step["name"], item_id, "skipped", str(value))
        self.print(format_row(step["name"], item_id, "skipped", str(value)))
        return False

    def _map(self, run_id, step, item, item_id) -> bool:
        with open(step["rules"]) as f:
            rules = json.load(f)
        field = step["field"]
        value = rules.get(item.get(field), "unrouted")
        detail = f"{item.get(field)} -> {value}"
        self.store.record_step(run_id, step["name"], item_id, "ok", detail)
        self.print(format_row(step["name"], item_id, "ok", detail))
        return True

    # ---- model: the real AI decision ----
    def _model(self, run_id, step, item, item_id) -> bool:
        prior = self.store.get_step(run_id, step["name"], item_id)
        if prior:
            return prior["result"] in step.get("proceed_when", [])
        with open(step["prompt"]) as f:
            prompt = f.read()
        # A model that returns a verdict outside the configured outcomes (or
        # no response at all) is a contract violation. Route it through the
        # same controlled halt as on_unknown -- one "the platform doesn't
        # know what to do here" mechanism, never a silent crash that leaves
        # the run stuck at "running".
        try:
            verdict, reason = self.model.judge(item_id, item, prompt, step["outcomes"])
        except (ValueError, KeyError) as e:
            detail = f"model decision failed for {item_id}: {e}"
            self.store.record_step(run_id, step["name"], item_id, "error", detail)
            self.print(format_row(step["name"], item_id, "error", detail))
            raise RunStopped(detail)
        self.store.record_step(run_id, step["name"], item_id, verdict, reason)
        self.print(format_row(step["name"], item_id, verdict, reason))
        return verdict in step.get("proceed_when", [])
