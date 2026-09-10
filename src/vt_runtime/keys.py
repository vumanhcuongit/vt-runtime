"""Idempotency key derivation.

The key is DERIVED from stable identity, never generated per attempt.
    key = vt:workflow:run_id:item_id
run_id identifies the RUN, not the attempt, so every retry of a run
produces an identical key and the existing ledger record is found.
"""


def build_key(vt: str, workflow: str, run_id: str, item_id: str) -> str:
    return f"{vt}:{workflow}:{run_id}:{item_id}"
