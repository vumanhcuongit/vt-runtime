"""Safe external actions -- the only part of the platform that touches the
outside world, isolated here on purpose.

Three-state lifecycle per intended action, plus an orthogonal approval
gate:

    approval_status:  pending -> approved
    action_status:    none -> intent -> committed

`intent` means neither "done" nor "not done" -- it means *go and find
out*, via reconcile. The idempotency key is DERIVED from stable identity
(vt:workflow:run_id:item_id), never generated per attempt, so every retry
of a run finds the same ledger record.
"""
import os

from adapters.external import (
    ExternalUnavailable, RECONCILE_YES, RECONCILE_NO, RECONCILE_UNKNOWN,
)
from core.observability import format_row


class RunStopped(Exception):
    """Halt the run deliberately (unknown lookup, or reconcile cannot-answer)."""


def derive_key(vt, workflow, run_id, step, item_id) -> str:
    # run_id identifies the RUN, not the attempt -- that is what makes a
    # retry reuse the same key instead of minting a fresh one. The step name
    # is included so a workflow with two external actions on the same item
    # (e.g. "create a task" AND "post a note") gets two distinct keys instead
    # of the second silently reading the first's ledger row. This extends the
    # brief's vt:workflow:run_id:item_id formula by one segment for that case.
    return f"{vt}:{workflow}:{run_id}:{step}:{item_id}"


def perform_external(store, adapter, run_id, item_id, key, step, *,
                     approval_override, crash_at, printer):
    """Execute an external step exactly once across crashes and retries."""
    name = step["name"]
    approval_mode = approval_override or step.get("approval", "auto")

    action = store.get_action(key)
    if action is None:
        approval_status = "approved" if approval_mode == "auto" else "pending"
        store.create_action(key, run_id, approval_status, "none")
        action = store.get_action(key)

    # approval gate -- a separate axis: an action awaiting a person has
    # definitely not happened, so it can never be `intent`.
    if action["approval_status"] != "approved":
        store.upsert_step(run_id, name, item_id, "pending",
                          "waiting for human approval")
        printer(format_row(name, item_id, "pending", "waiting for human approval"))
        return

    if action["action_status"] == "committed":
        store.upsert_step(run_id, name, item_id, "committed",
                          f"{action['result']}  key {key}")
        printer(format_row(name, item_id, "committed",
                           f"{action['result']}  key {key}"))
        return

    if action["action_status"] == "intent":
        _reconcile(store, adapter, run_id, item_id, key, step, printer)
        return

    # action_status == "none", approved -> first real attempt
    store.set_action_status(key, "intent")
    if crash_at == "A":
        os._exit(137)  # crashed AFTER intent, BEFORE the external call
    result = _execute_or_halt(store, adapter, run_id, item_id, key, step, printer)
    if crash_at == "B":
        os._exit(137)  # crashed AFTER the external call, BEFORE committing
    store.set_action_status(key, "committed", result)
    store.upsert_step(run_id, name, item_id, "committed", f"{result}  key {key}")
    printer(format_row(name, item_id, "committed", f"{result}  key {key}"))


def _execute_or_halt(store, adapter, run_id, item_id, key, step, printer):
    # If the target is unreachable on a first attempt, don't let the raw
    # exception escape and strand the run at "running" -- the action is
    # already recorded as `intent`, so a later retry will reconcile it. Halt
    # cleanly through the same mechanism as every other unresolved case.
    try:
        return adapter.execute(step, key)
    except ExternalUnavailable as e:
        detail = (f"external system unavailable for key {key} ({e}); "
                  f"left as intent, run stopped for human review")
        store.upsert_step(run_id, step["name"], item_id, "intent", detail)
        printer(format_row(step["name"], item_id, "intent", detail))
        raise RunStopped(detail)


def _reconcile(store, adapter, run_id, item_id, key, step, printer):
    name = step["name"]
    outcome, existing = adapter.reconcile(key)
    if outcome == RECONCILE_YES:
        store.set_action_status(key, "committed", existing)
        detail = f"task {existing} already existed, not created again"
        store.upsert_step(run_id, name, item_id, "reconciled", detail)
        printer(format_row(name, item_id, "reconciled", detail))
        return
    if outcome == RECONCILE_NO:
        result = _execute_or_halt(store, adapter, run_id, item_id, key, step, printer)
        store.set_action_status(key, "committed", result)
        detail = f"not found on reconcile -> created on retry ({result})"
        store.upsert_step(run_id, name, item_id, "reconciled", detail)
        printer(format_row(name, item_id, "reconciled", detail))
        return
    # RECONCILE_UNKNOWN: external down -> leave as intent, stop, report
    detail = (f"external system cannot answer for key {key}; "
              f"left as intent, run stopped for human review")
    store.upsert_step(run_id, name, item_id, "intent", detail)
    printer(format_row(name, item_id, "intent", detail))
    raise RunStopped(detail)
