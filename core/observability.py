"""Observability: one shared row format + a state dump.

Part 4 asks that a reader be able to answer, with no explanation: what
ran, what was decided, what action happened, did it succeed. Runner state
(runs/steps) and the external-action ledger are rendered as separate
blocks because they have different lifecycles.

The runner's live output and `inspect` share `format_row`, so they line
up exactly. Nothing here knows about any specific workflow's domain.
"""

_STEP_W, _ITEM_W, _RESULT_W = 12, 12, 12


def step_header() -> str:
    return f"  {'STEP':<{_STEP_W}} {'ITEM':<{_ITEM_W}} {'RESULT':<{_RESULT_W}} DETAIL"


def format_row(step, item, result, detail) -> str:
    return (f"  {step:<{_STEP_W}} {(item or '-'):<{_ITEM_W}} "
            f"{result:<{_RESULT_W}} {detail or ''}")


def render(store) -> str:
    lines = []
    for run in store.list_runs():
        lines.append("")
        lines.append(
            f"RUN {run['run_id']}   workflow: {run['vt']}/{run['workflow']}"
            f"   status: {run['status']}"
        )
        if run["stopped_reason"]:
            lines.append(f"  reason: {run['stopped_reason']}")
        lines.append("")
        lines.append(step_header())
        for s in store.list_steps(run["run_id"]):
            lines.append(
                format_row(s["step_name"], s["item_id"], s["result"], s["detail"])
            )
    lines.append("")
    lines.append("EXTERNAL ACTIONS")
    lines.append(f"  {'key':<52} {'state':<11} result")
    for a in store.list_actions():
        lines.append(
            f"  {a['idempotency_key']:<52} {a['action_status']:<11} {a['result'] or ''}"
        )
    return "\n".join(lines)
