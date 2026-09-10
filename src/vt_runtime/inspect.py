"""Human-readable state dump.

Runner state (runs/steps) and the external-action ledger are rendered as
separate blocks: they have different lifecycles, and a reader should be
able to answer at a glance -- what ran, what was decided, what action
happened, did it succeed.
"""


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
        lines.append(f"  {'STEP':<11} {'ITEM':<10} {'RESULT':<11} DETAIL")
        for s in store.list_steps(run["run_id"]):
            item = s["item_id"] or "-"
            lines.append(
                f"  {s['step_name']:<11} {item:<10} {s['result']:<11} {s['detail'] or ''}"
            )
    lines.append("")
    lines.append("EXTERNAL ACTIONS")
    lines.append(f"  {'key':<52} {'state':<11} result")
    for a in store.list_actions():
        lines.append(
            f"  {a['idempotency_key']:<52} {a['action_status']:<11} {a['result'] or ''}"
        )
    return "\n".join(lines)
