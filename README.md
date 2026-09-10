# VT Runtime

A reusable **workflow runner** with durable state that recovers from a crash
without repeating an external action.

## 1. What this is

Musea's Virtual Talents (VTs) each re-solved the same infrastructure problems:
how to run steps in order, record what happened, and — the dangerous one — how
to avoid performing an external action twice after a crash. This is **one shared
piece of machinery** that any VT can use: a config-driven runner with durable
SQLite state and crash-safe external actions. It is **not** Moza, not Helios,
not a platform — just the runner underneath them. Moza's song screening is the
demonstration workflow, not the thing being built.

## 2. Setup

Requires **Python 3.12+**. **No dependencies. No API key.**

```bash
git clone <this-repo> vt-runtime && cd vt-runtime
make demo
```

That's it — the demo replays recorded model responses, so nothing to install and
no key to set.

## 3. The commands

| Command | What it shows |
|---|---|
| `make demo` | A normal run start to finish: one task per `include`; stops cleanly at the song with unknown rights. |
| `make demo-crash-a` | Crash **after** writing `intent`, **before** the external call — then retry. Task is created exactly once ("created on retry"). |
| `make demo-crash-b` | Crash **after** the external call, **before** recording completion — then retry. The task is **not** created again ("already existed"). |
| `make demo-crash-b-down` | Crash B, then retry with the external system **down**. Record stays `intent`, the run stops, and a report is printed for a human. |
| `make demo-approval` | `--approval=required`: the external action waits for a person instead of running. One flag, no code change. |
| `make demo-two-runs` | Two **different** runs over the same songs create **two** tasks — correct, not a bug. |
| `make inspect` | Every run, every step, every external action. |
| `make reset` | Delete local state for a clean slate. |
| `make test` | Run the full unittest suite (no key, no network for the crash/logic tests). |

Each crash command performs the crash **and** the retry in one command — nothing
for a reviewer to run by hand mid-demo.

## 4. The three states and reconcile

Every external action has exactly one record, in one of three states:

| State | Meaning | What a retry does |
|---|---|---|
| *(no record)* | Never attempted | Do it |
| `intent` | Attempted, outcome unknown | **Reconcile** — ask the external system |
| `committed` | Confirmed done | Skip, return the stored result |

A crash can land between "task created" and "we recorded that it was created".
From the record alone, "crashed before the call" and "crashed after the call"
both leave a row saying `intent`. So **`intent` means neither "done" nor "not
done" — it means: go and find out.**

```
  record says "intent"
        │
        ▼
  ask external system: "does a task with key K exist?"
        │
        ├── yes ────────► mark committed, do NOT create again
        ├── no ─────────► create it now, then mark committed
        └── cannot answer ► leave as intent, STOP, print a report for a human
```

That third branch is why `--external=down` doesn't corrupt anything: if we can't
get a straight answer, we don't guess and we don't retry blindly — we stop and
tell someone, with the key they need to check by hand.

**Approval is a separate axis, not a fourth state.** An action awaiting a person
has definitely not happened, so it can't be `intent` (which means it *might*
have). Two independent fields:

```
  approval_status:  pending → approved
  action_status:    none → intent → committed
```

With `approval: auto` approval is granted immediately; with `approval: required`
the action sits at `pending`/`none` and nothing is attempted until a person
approves. This is why the announced change request — *an autonomous action now
needs approval* — is a one-line config change, not new code: the gate already
exists and the action state machine is untouched.

## 5. The idempotency key

The key is **derived, never generated**:

```
  key = vt : workflow : run_id : item_id
  moza:song_screening:run_2026_03_14:song_042
```

- `vt` + `workflow` — two workflows can't collide.
- `run_id` — identifies the **run**, not the attempt.
- `item_id` — the thing being acted on (`song_id` here; `candidate_id` for Helios — it comes from config).

**The run_id rule (the part that's easy to get wrong):** a run_id is created once
when a run starts and reused by every retry of that run. Only a genuinely new run
(started by a person or schedule) gets a new id.

**What breaks if you generate a fresh id per attempt:** the key changes on retry,
the existing `intent`/`committed` record is never found, and a duplicate task is
created — even though the reconcile logic is perfectly correct. The whole
mechanism does nothing. Two situations, two correct answers:

| Situation | Same key? | Correct outcome |
|---|---|---|
| Same run + same song, run twice (a retry) | Yes | **One** task |
| Two different runs, same song | No | **Two** tasks (legitimate) |

`make demo-crash-a`/`-b` print the key so you can confirm it's identical across a
retry; `make demo-two-runs` shows the two-runs-two-tasks case.

## 6. Where an LLM is not used, and why

The Moza config has two steps that both *look* like questions a model could
answer:

- **`rights_check` is a deterministic lookup**, on purpose. A made-up licence is a
  legal problem, not a quality one. When the registry can't answer, the run
  **stops** rather than letting a model guess something that looks like a lookup
  result.
- **`judge` is a real model decision.** "Is this song good for teaching a
  beginner?" has no lookup table, and its answer changes what happens next
  (`include` creates a task; `exclude`/`needs_review` do not).

**The condition that would flip the rights check:** this is a rule, not an
absolute. The prototype treats rights as authoritative structured data *because a
registry exists*. Where one doesn't — and in some markets it won't — rights
arrive as documents and contracts, and extraction becomes a **model step followed
by human review** for anything uncertain. What never changes: a model must not
fabricate something that looks like a lookup result.

## 7. The second config (Helios) — present, not executed

`configs/helios_recruiting_screening.json` is included to show the runner is
reusable through configuration, not hard-coded to songs. Note the differences
from Moza:

- Different identifier (`candidate_id` vs `song_id`).
- Different step **order** — Helios judges *before* routing; Moza checks rights
  *before* judging.
- Different external system (`ats` vs `review_system`) and different approval
  default (`required` vs `auto`).

**It has not been run.** The first thing I'd expect to break if it were: the
external step targets `ats`, but only a `review_system` adapter is wired, and the
`route` step is a `deterministic` type the runner doesn't yet have a handler for.
Making Helios run is a config-plus-adapter delta (add an `ATS` adapter behind the
same `execute`/`reconcile` interface, and a `route` handler) — **not** a change to
the runner's core state machine, which is the point.

## 8. What is mocked, and what that hides

| Real thing | Mock |
|---|---|
| Song source | `fixtures/songs.json` (10 songs) |
| Rights registry | `fixtures/rights.json` (lookup with one deliberate `unknown`) |
| The model | `fixtures/model_responses.json`, recorded from real calls (see below) |
| Review system | Local SQLite (`state/review_system.db`) with `create_task` / `find_task_by_key` |

The runner talks to an **adapter**, never a named system. One interface, two
methods:

```
  execute(action, idempotency_key)   → perform the action
  reconcile(idempotency_key)         → "did this already happen?"  yes / no / cannot_answer
```

Swapping the target (review system → ATS → chat) is a new adapter plus config,
not a runner change. **That interface is the platform boundary.**

**Model fixtures** were recorded from a real free model via OpenRouter using only
`tools/record_fixtures.py` (stdlib `urllib`, no SDK). Re-record with:

```bash
OPENROUTER_API_KEY=... python3 tools/record_fixtures.py
```

The recorder skips already-recorded songs and saves after each success, so it can
accumulate across the free tier's rate limits. The **demo never needs a key** —
it replays the recorded file. A `LiveModel` stub exists as the concrete home for
a real, un-replayed call; wiring it is out of scope for this prototype.

## 9. Known limitations

- **Single process.** This demonstrates safe *sequential* recovery. It does not
  solve concurrent workers: two processes could each `reconcile`, both get "no",
  and both create. Fixing that needs a lock or native idempotency at the target —
  out of scope for a small prototype.
- **Reconcile depends on lookup-by-key.** It only works if the external system
  can answer "did action K happen?". In production, prefer a target that accepts
  an idempotency key **natively** so the *system* deduplicates; lookup-and-
  reconcile is the fallback for systems that can't.
- **`idempotent: true` is a declaration the runner trusts.** A real platform
  should *refuse* an external action that doesn't carry an idempotency contract,
  rather than taking the config's word for it.
- **State lives in local SQLite files** under `state/`.
- **The Helios config has not been executed** (see §7).

## 10. Deviations from the spec's examples (deliberate, and why)

- **Config is JSON, not YAML.** The spec shows YAML; JSON keeps the runtime at
  **zero dependencies** so the clean-clone / no-key gate has nothing to install.
- **`fetch` orders the unknown-rights song last.** One fixture set then
  demonstrates both "exactly one task per `include`" and "unknown rights stops the
  run" in a single `make demo`.

## Project layout

```
configs/    workflow definitions (moza runs; helios is shape-only)
fixtures/   canned data standing in for real systems
prompts/    model prompts
src/vt_runtime/
  keys.py      derived idempotency key
  state.py     durable SQLite state (runs/steps + external_actions ledger)
  adapter.py   execute/reconcile boundary (review-system mock)
  model.py     replay/live model providers
  config.py    JSON config loader
  runner.py    the reusable runner (state machine, resume, reconcile)
  inspect.py   human-readable state dump
  cli.py       command-line entry point
tools/record_fixtures.py   one-time model-fixture recorder (stdlib only)
tests/      unittest suite (logic + cross-process crash recovery)
```
