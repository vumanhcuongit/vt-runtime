# Failure & Recovery — a step-by-step walkthrough

This is the scenario the brief names: an external action succeeds, the
process dies before recording completion, the run is retried, and the
action must not happen twice. One command runs the whole thing:

```bash
make demo-crash-b
```

It scopes the run to a single licensed, `include` song (`song_041`) so the
message stays crisp: **one** task should exist, and after a crash + retry,
exactly one does.

---

## Crash point B — the hard case

```
  judge says include
        │
        ▼
  write action_status = intent            (runner state)
        │
        ▼
  external system creates task  → T-991   (external state)   ◄── CRASH POINT B: os._exit(137)
        │
        ▼
  write action_status = committed         ← never reached
```

At the crash, the two stores disagree:

| Store | Says |
|---|---|
| external system (`review_system.db`) | task **T-991 exists** |
| runner ledger (`runtime.db`) | action is still **`intent`** |

`intent` means *attempted, outcome unknown* — neither "done" nor "not
done". From the record alone you cannot tell crash-A (call never happened)
from crash-B (call happened). So the retry does not guess.

---

## The retry — reconcile, don't redo

```
  retry reaches create_task for song_041
        │
        ▼
  ledger says "intent"  → RECONCILE
        │
        ▼
  ask review system: does an action with key
  "moza:song_screening:run_crash_b:song_041" exist?
        │
        └── yes, T-991 ──► mark committed, do NOT create again
```

Because the idempotency key is **derived** (`vt:workflow:run_id:item_id`)
and the retry reuses the same `run_id`, the key is identical to the one the
first attempt used — so reconcile finds the existing task instead of making
a second.

Output on the retry:

```
  create_task  song_041     reconciled   task T-991 already existed, not created again
```

The word **reconciled** is doing the work: the retry did not blindly skip
and did not blindly redo — it asked, then recorded the truth.

---

## What you can inspect afterwards

```
EXTERNAL ACTIONS
  key                                                  state       result
  moza:song_screening:run_crash_b:song_041             committed   T-991
```

One row. One task (`T-991`). Run `make demo-crash-b` three times: still one
task, because once `committed`, a retry returns the stored result and does
nothing.

---

## The variant: external system unreachable

```bash
make demo-crash-b-down
```

Same crash, but the retry runs with the review system **down**. Reconcile
now returns *cannot answer* — so the runner does the only safe thing:

```
  create_task  song_041     intent   external system cannot answer ...; left as intent, run stopped for human review
```

The action is **left as `intent`**, the run **stops**, and a report is
printed. Nothing is retried blindly, nothing is assumed. A human (or a
later retry once the system is back) picks up exactly where it paused.
