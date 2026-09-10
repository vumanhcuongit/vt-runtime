"""The reusable workflow runner.

Reads steps from config, executes them in order, records durable state,
and -- on retry after a crash -- reconciles external actions instead of
blindly repeating or skipping them.

Recording that a run finished is done HERE, by the platform, not by a
config step. If completion were a workflow step, the workflow would own
tracking its own completion -- which is exactly the responsibility this
runner exists to take over. That is why there is no RESULT step.
"""
import json
import os

from .keys import build_key
from .adapter import RECONCILE_YES, RECONCILE_NO, RECONCILE_UNKNOWN
from .inspect import format_row


class RunStopped(Exception):
    """Halt the run deliberately (unknown rights, or reconcile cannot-answer)."""


class Runner:
    def __init__(self, config, store, adapter, model, *,
                 crash_at=None, approval_override=None, printer=print):
        self.cfg = config
        self.store = store
        self.adapter = adapter
        self.model = model
        self.crash_at = crash_at
        self.approval_override = approval_override
        self.print = printer
        self.vt = config["vt"]
        self.workflow = config["workflow"]
        self.item_id_field = config["item_id_field"]
        self.steps = config["steps"]

    def _step(self, name):
        for s in self.steps:
            if s["name"] == name:
                return s
        return None

    def run(self, run_id: str) -> str:
        existing = self.store.get_run(run_id)
        if existing and existing["status"] == "completed":
            self.print(f"RUN {run_id} already completed; nothing to do.")
            return "completed"
        self.store.create_run(run_id, self.vt, self.workflow)

        try:
            items = self._fetch(run_id)
            for item in items:
                item_id = item[self.item_id_field]
                if not self._rights_check(run_id, item_id):
                    continue  # skipped (not licensed)
                verdict = self._judge(run_id, item_id, item)
                if verdict != "include":
                    continue  # exclude / needs_review => no external action
                self._create_task(run_id, item_id, item)
        except RunStopped as stop:
            self.store.set_run_status(run_id, "stopped", str(stop))
            self.print(f"RUN {run_id} STOPPED: {stop}")
            return "stopped"

        self.store.set_run_status(run_id, "completed")
        self.print(f"RUN {run_id} completed.")
        return "completed"

    # ---- run-level: fetch ----
    def _fetch(self, run_id):
        step = self._step("fetch")
        with open(step["source"]) as f:
            items = json.load(f)
        # The idempotency key needs a stable identifier from every item. If one
        # is missing we STOP at fetch -- we do NOT fall back to the list index,
        # which looks fine until the source reorders items and silently produces
        # duplicate/mismatched keys in production.
        for pos, item in enumerate(items):
            if self.item_id_field not in item:
                reason = (f"item at position {pos} is missing required field "
                          f"'{self.item_id_field}'; stopped at fetch "
                          f"(no list-index fallback)")
                self.store.record_step(run_id, "fetch", None, "stopped", reason)
                raise RunStopped(reason)
        if not self.store.get_step(run_id, "fetch", None):
            self.store.record_step(run_id, "fetch", None, "ok", f"{len(items)} items")
            self.print(format_row("fetch", None, "ok", f"{len(items)} items"))
        return items

    # ---- item-level: rights_check ----
    def _rights_check(self, run_id, item_id) -> bool:
        step = self._step("rights_check")
        if not step:
            return True
        prior = self.store.get_step(run_id, "rights_check", item_id)
        if prior:
            return prior["result"] == "ok"
        with open(step["lookup"]) as f:
            rights = json.load(f)
        status = rights.get(item_id, "unknown")
        if status == "licensed":
            self.store.record_step(run_id, "rights_check", item_id, "ok", "licensed")
            self.print(format_row("rights_check", item_id, "ok", "licensed"))
            return True
        if status == "unknown" and step.get("on_unknown") == "stop":
            reason = f"rights unknown for {item_id}: registry did not answer -> run stopped"
            self.store.record_step(run_id, "rights_check", item_id, "unknown", reason)
            self.print(format_row("rights_check", item_id, "unknown", reason))
            raise RunStopped(reason)
        # not_licensed, or unknown with skip policy
        self.store.record_step(run_id, "rights_check", item_id, "skipped", status)
        self.print(format_row("rights_check", item_id, "skipped", status))
        return False

    # ---- item-level: judge (model) ----
    def _judge(self, run_id, item_id, item):
        step = self._step("judge")
        prior = self.store.get_step(run_id, "judge", item_id)
        if prior:
            return prior["result"]
        with open(step["prompt"]) as f:
            prompt = f.read()
        verdict, reason = self.model.judge(item_id, item, prompt, step["outcomes"])
        self.store.record_step(run_id, "judge", item_id, verdict, reason)
        self.print(format_row("judge", item_id, verdict, reason))
        return verdict

    # ---- item-level: create_task (external) ----
    def _create_task(self, run_id, item_id, item):
        step = self._step("create_task")
        key = build_key(self.vt, self.workflow, run_id, item_id)
        approval_mode = self.approval_override or step.get("approval", "auto")

        action = self.store.get_action(key)
        if action is None:
            approval_status = "approved" if approval_mode == "auto" else "pending"
            self.store.create_action(key, run_id, approval_status, "none")
            action = self.store.get_action(key)

        # approval gate -- separate axis from action_status
        if action["approval_status"] != "approved":
            self.store.record_step(run_id, "create_task", item_id, "pending",
                                   "waiting for human approval")
            self.print(format_row("create_task", item_id, "pending",
                                   "waiting for human approval"))
            return

        if action["action_status"] == "committed":
            self.store.record_step(run_id, "create_task", item_id, "committed",
                                   f"{action['result']}  key {key}")
            self.print(format_row("create_task", item_id, "committed",
                                   f"{action['result']}  key {key}"))
            return

        if action["action_status"] == "intent":
            return self._reconcile(run_id, item_id, key)

        # action_status == "none", approved -> attempt for the first time
        self.store.set_action_status(key, "intent")
        if self.crash_at == "A":
            os._exit(137)  # crashed AFTER intent, BEFORE the external call
        result = self.adapter.execute(step, key)
        if self.crash_at == "B":
            os._exit(137)  # crashed AFTER the external call, BEFORE committing
        self.store.set_action_status(key, "committed", result)
        self.store.record_step(run_id, "create_task", item_id, "committed",
                               f"{result}  key {key}")
        self.print(format_row("create_task", item_id, "committed",
                               f"{result}  key {key}"))

    def _reconcile(self, run_id, item_id, key):
        outcome, existing = self.adapter.reconcile(key)
        if outcome == RECONCILE_YES:
            self.store.set_action_status(key, "committed", existing)
            detail = f"task {existing} already existed, not created again"
            self.store.record_step(run_id, "create_task", item_id, "reconciled", detail)
            self.print(format_row("create_task", item_id, "reconciled", detail))
            return
        if outcome == RECONCILE_NO:
            result = self.adapter.execute(self._step("create_task"), key)
            self.store.set_action_status(key, "committed", result)
            detail = f"not found on reconcile -> created on retry ({result})"
            self.store.record_step(run_id, "create_task", item_id, "reconciled", detail)
            self.print(format_row("create_task", item_id, "reconciled", detail))
            return
        # RECONCILE_UNKNOWN: external system down -> leave intent, stop, report
        detail = (f"external system cannot answer for key {key}; "
                  f"left as intent, run stopped for human review")
        self.store.record_step(run_id, "create_task", item_id, "intent", detail)
        self.print(format_row("create_task", item_id, "intent", detail))
        raise RunStopped(detail)
