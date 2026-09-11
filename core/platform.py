"""The reusable capability: a thin write-boundary every protected external
action goes through.

This is the piece the case study (Parts 1-3) proposes for the *first*
migration: identity + decision record + write-once + approval enforcement,
exposed as one call a VT makes from its own code —

    platform.write(vt, workflow, run_id, item_id, operation, target, payload)

It owns the boundary, not the workflow's logic. It derives the idempotency
key (never generates it), enforces the approval gate, and makes a protected
write with **double-run protection**: it records `intent` before the call
and, on retry after a crash, **reconciles** (asks the target "did K
happen?") rather than blindly redoing or skipping.

The precise guarantee is at-least-once execution, made *effectively*-once
by target-side lookup, and **single-writer only** — not distributed
exactly-once. If the target cannot answer lookups by key, or two workers
race the same key, the "effectively-once" property does not hold.

The step runner in this repo is a *harness* that calls this; a real VT
would call it in place of its raw external call, its own logic unchanged.
"""
import os

from adapters.external import (
    ExternalUnavailable, RECONCILE_YES, RECONCILE_NO, RECONCILE_UNKNOWN,
)
from core.observability import format_row


class RunStopped(Exception):
    """Halt deliberately (unknown lookup, unreachable/unanswerable target)."""


def derive_key(vt, workflow, run_id, operation, item_id) -> str:
    # DERIVED from stable identity, never generated per attempt. run_id is the
    # trigger instance (identifies the run, not the attempt), so every retry
    # reproduces the same key. `operation` distinguishes two external actions
    # on the same item; `item_id` scopes to the thing acted on.
    return f"{vt}:{workflow}:{run_id}:{operation}:{item_id}"


class Platform:
    """The write-boundary. Holds the ledger and the target adapters; the
    workflow passes in the identity of the intended business action."""

    def __init__(self, store, adapters, *, printer=print):
        self._store = store
        self._adapters = adapters      # dict: target -> ExternalAdapter
        self._print = printer

    def write(self, *, vt, workflow, run_id, item_id, operation, target,
              payload=None, approval="auto", crash_at=None):
        """Perform one protected external action with double-run protection
        across a crash + retry (write-intent, then reconcile-on-retry).
        Effectively-once given a target that answers lookup-by-key;
        single-writer only. Raises RunStopped when it must halt for a human."""
        if target not in self._adapters:
            raise RunStopped(f"no adapter registered for target '{target}' "
                             f"(operation '{operation}')")
        adapter = self._adapters[target]
        key = derive_key(vt, workflow, run_id, operation, item_id)

        action = self._store.get_action(key)
        if action is None:
            approval_status = "approved" if approval == "auto" else "pending"
            self._store.create_action(key, run_id, approval_status, "none")
            action = self._store.get_action(key)

        # approval gate -- a separate axis: an action awaiting a person has
        # definitely not happened, so it can never be `intent`.
        if action["approval_status"] != "approved":
            self._record(run_id, operation, item_id, "pending",
                         "waiting for human approval")
            return

        if action["action_status"] == "committed":
            self._record(run_id, operation, item_id, "committed",
                         f"{action['result']}  key {key}")
            return

        if action["action_status"] == "intent":
            self._reconcile(run_id, item_id, key, operation, adapter, payload)
            return

        # action_status == "none", approved -> first real attempt
        self._store.set_action_status(key, "intent")
        if crash_at == "A":
            os._exit(137)  # crashed AFTER intent, BEFORE the external call
        result = self._execute_or_halt(adapter, payload, key, run_id, item_id, operation)
        if crash_at == "B":
            os._exit(137)  # crashed AFTER the external call, BEFORE committing
        self._store.set_action_status(key, "committed", result)
        self._record(run_id, operation, item_id, "committed", f"{result}  key {key}")

    # ---- internals ----
    def _record(self, run_id, operation, item_id, result, detail):
        # upsert so the audit row reflects the latest state (intent ->
        # reconciled -> committed), matching the ledger after a recovery.
        self._store.upsert_step(run_id, operation, item_id, result, detail)
        self._print(format_row(operation, item_id, result, detail))

    def _execute_or_halt(self, adapter, payload, key, run_id, item_id, operation):
        # A target unreachable on a first attempt must not escape as a
        # traceback and strand the run at "running" -- the action is already
        # `intent`, so a later retry reconciles it. Halt cleanly instead.
        try:
            return adapter.execute(payload, key)
        except ExternalUnavailable as e:
            detail = (f"external system unavailable for key {key} ({e}); "
                      f"left as intent, run stopped for human review")
            self._record(run_id, operation, item_id, "intent", detail)
            raise RunStopped(detail)

    def _reconcile(self, run_id, item_id, key, operation, adapter, payload):
        outcome, existing = adapter.reconcile(key)
        if outcome == RECONCILE_YES:
            self._store.set_action_status(key, "committed", existing)
            self._record(run_id, operation, item_id, "reconciled",
                         f"task {existing} already existed, not created again")
            return
        if outcome == RECONCILE_NO:
            result = self._execute_or_halt(adapter, payload, key, run_id, item_id, operation)
            self._store.set_action_status(key, "committed", result)
            self._record(run_id, operation, item_id, "reconciled",
                         f"not found on reconcile -> created on retry ({result})")
            return
        # RECONCILE_UNKNOWN: target can't answer -> leave intent, stop, report
        detail = (f"external system cannot answer for key {key}; "
                  f"left as intent, run stopped for human review")
        self._record(run_id, operation, item_id, "intent", detail)
        raise RunStopped(detail)
