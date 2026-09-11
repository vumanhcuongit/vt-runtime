# VT Runtime

One reusable platform capability: a **config-driven workflow runner** with
durable state that recovers from a crash without repeating an external action —
and that runs **more than one Virtual Talent through configuration, not code.**

## 1. What this is

Musea's Virtual Talents (VTs) each re-solved the same infrastructure: run steps
in order, record what happened, gate risky actions, and — the dangerous one —
avoid performing an external action twice after a crash. This is the shared
machinery underneath them: **reliable workflow execution with safe external
actions.** It is deliberately **not** Moza, not Helios, not "the platform" —
just the runner. Two VTs (Moza song-screening and Helios recruiting-screening)
run on it from config alone, to show the capability generalizes.

## 2. Setup

Requires **Python 3.12+**. **No dependencies. No API key. No database to install.**

```bash
git clone <this-repo> vt-runtime && cd vt-runtime
make demo
```

SQLite ships compiled inside CPython (the `sqlite3` stdlib module), so there is
no database server or package to add. Every `make` target first runs a
`preflight` check that verifies `python3` and its bundled `sqlite3` and fails
with a clear message if not — cheap insurance on a demo machine you don't
control. The demo replays recorded model responses, so no key is ever needed.

## 3. Structure, and why it's the first piece of evidence

Part 4 asks for **one reusable capability** and, in the same breath, *don't
rebuild the VTs*. That's a burden of proof: the layout should let a reader
**see** the boundary before reading any code.

```
vt-runtime/
├── cli.py                      thin entry point (the Makefile calls this)
├── core/                       ← the reusable capability; ZERO domain words
│   ├── runner.py               execution: dispatch steps by TYPE, not by name
│   ├── state.py                durable SQLite: runs/steps + external_actions ledger
│   ├── actions.py              safe external actions: 3-state lifecycle + key + approval + reconcile
│   └── observability.py        the state dump every command prints
├── adapters/                   ← the outside world, behind interfaces
│   ├── model.py                ModelProvider | ReplayModel | LiveModel
│   └── external.py             ExternalAdapter | ReviewSystemAdapter | AtsAdapter
├── workflows/                  ← what each VT wants done (data, not code)
│   ├── moza_song_screening/    config.json · teaching_suitability.txt · fixtures/
│   └── helios_recruiting_screening/  config.json · candidate_screen.txt · fixtures/
├── tools/record_fixtures.py    one-time, config-driven model-fixture recorder
├── tests/                      test_crash_recovery · test_idempotency · test_runner · test_adapters
└── examples/failure-recovery.md
```

**The dependency direction *is* the argument:**

```
  workflows/  ──depends on──►  core/  ──depends on──►  adapters/
```

Never the reverse. `core/` imports nothing from `workflows/`; `adapters/`
imports nothing from `core/`. The mechanical proof: `grep -rniE
"song|candidate|tempo|licen" core/` returns **nothing**. If a domain word ever
appears in `core/`, the boundary has leaked in the direction that turns a
platform back into one VT's code.

**Each workflow is a folder** (config + prompt + fixtures together), not files
scattered into shared `prompts/` and `fixtures/` dirs. This passes the deletion
test (removing a VT is removing one directory, no orphans) and the addition test
(see §8).

**Why `core/` and not `platform/`:** a top-level `platform/` directory shadows
Python's standard-library `platform` module on `sys.path`, which can break on
another machine — exactly the failure mode this project avoids. `core/` carries
the same "reusable capability" meaning without the collision.

## 4. The commands

| Command | What it shows |
|---|---|
| `make demo` | Moza, start to finish: one task per `include`; `exclude`/`needs_review` create nothing; not-licensed skipped; stops cleanly at unknown rights. |
| `make demo-crash-a` | Crash **after** `intent`, **before** the call — then retry. Task created exactly once ("created on retry"). |
| `make demo-crash-b` | Crash **after** the call, **before** commit — then retry. Task **not** created again ("already existed"). |
| `make demo-crash-b-down` | Crash B, then retry with the external system **down**: record stays `intent`, run stops, prints a report for a human. |
| `make demo-approval` | `--approval=required`: the external action waits for a person. One flag, no code change. |
| `make demo-two-runs` | Two **different** runs, same songs → **two** tasks. Correct, not a bug. |
| `make demo-missing-id` | An item missing its `song_id` → the run **stops at fetch**; never falls back to list position. |
| `make demo-helios` | The **same runner** executes a different VT — different steps, identifier, target, approval — from config alone. |
| `make inspect` / `make reset` / `make test` | Dump all state / clear state / run the suite. |

> The crash demos run against a single-include-song source so the crux — "one
> task, never two" — reads cleanly. `examples/failure-recovery.md` is a
> step-by-step walkthrough of the crash-B run.

## 5. The three states and reconcile

Every external action has one record, in one of three states:

| State | Meaning | What a retry does |
|---|---|---|
| *(no record)* | Never attempted | Do it |
| `intent` | Attempted, outcome unknown | **Reconcile** — ask the external system |
| `committed` | Confirmed done | Skip, return the stored result |

A crash can land between "task created" and "we recorded it". Both crash-before
and crash-after leave a row saying `intent` — so **`intent` means neither "done"
nor "not done"; it means go and find out.**

```
  record says "intent"
        │
        ▼
  ask external system: "does an action with key K exist?"
        ├── yes ────────► mark committed, do NOT create again
        ├── no ─────────► create it now, then mark committed
        └── cannot answer ► leave as intent, STOP, print a report for a human
```

**Approval is a separate axis, not a fourth state** (`core/actions.py`):

```
  approval_status:  pending → approved
  action_status:    none → intent → committed
```

An action awaiting a person definitely hasn't happened, so it can never be
`intent`. This is why the announced change request — *an autonomous action now
needs approval* — is a one-line config change (`approval: auto → required`), not
new code.

**The runner enforces the idempotency contract**: an external step that doesn't
declare `idempotent: true` is refused before it can act — the platform doesn't
take the config's word for it. Everything the runner can't safely resolve —
unknown rights, a reconcile that can't answer, an **external system that's
unreachable on a first attempt**, a missing identifier, a model verdict outside
the configured `outcomes` — routes through the *same* controlled `stopped` state
with a readable reason (see §7), never a traceback that strands the run at
`running`.

## 6. The idempotency key

Derived, never generated:

```
  key = vt : workflow : run_id : step : item_id
  moza:song_screening:run_2026_03_14:create_task:song_042
```

`run_id` identifies the **run**, not the attempt, and every retry of a run
reuses it — so the key is identical across retries and the existing record is
found. Generate a fresh id per attempt instead and the key changes on retry, the
record isn't found, and a duplicate is created — the whole mechanism does
nothing.

For that reason the platform treats the `run_id` as the **trigger-instance id
and refuses to generate one**: `run` requires an explicit `--run-id`, and
`Runner.run` rejects an empty one. This matters most for the unattended case — a
cron/queue retry that forgot to pass a stable id would otherwise mint a new
run each time and duplicate every action. Identity is the trigger's to supply,
never the runner's to invent.

The `step` segment extends the brief's `vt:workflow:run_id:item_id` formula by
one part, so a workflow with **two external actions on the same item** (create a
task *and* post a note) gets two distinct keys instead of the second silently
reading the first's ledger row.

| Situation | Same key? | Correct outcome |
|---|---|---|
| Same run + song, run twice (a retry) | Yes | **One** task |
| Two different runs, same song | No | **Two** tasks (legitimate) |

**No identifier, no run.** `item_id` comes from the config field
`item_id_field`. If an item lacks it, the run **stops at fetch** — it never falls
back to list position, which looks fine until the source reorders and silently
breaks duplicate protection (`make demo-missing-id`).

## 7. Where an LLM is used, and where it isn't

Two Moza steps both *look* like model questions:

- **`rights_check` is a deterministic lookup**, on purpose. A made-up licence is
  a legal problem, not a quality one; when the registry can't answer, the run
  **stops** rather than letting a model invent a lookup result.
- **`judge` is a real model decision.** "Good for teaching a beginner?" has no
  lookup table, and its verdict changes what happens next (`include` → task;
  `exclude`/`needs_review` → nothing).

**The condition that flips it:** where rights arrive as documents rather than a
registry, extraction becomes a **model step behind a human gate**. What never
changes: a model must not fabricate something that looks like a lookup result.

**When the model breaks its contract** — a verdict outside the configured
`outcomes`, or no response for an item — the runner treats it exactly like
`on_unknown: stop`: it records an `error` step, halts the run with a readable
reason, and leaves it `stopped` (never a silent crash that strands the run at
`running`). One halt mechanism for everything the platform can't resolve.

## 8. Adding a workflow — does this structure make it easy?

Yes, and the repo proves it rather than claiming it. **Helios runs on the same
runner** (`make demo-helios`): a different step **order** (judge → route →
create_note), a different identifier (`candidate_id`), a different external
target (an ATS, producing `N-` notes), and a different approval default. No
`core/` or `adapters/` code changed to make Moza *or* Helios run — the runner
dispatches by step **type** (`deterministic` / `model` / `external`), and the
deterministic type covers both a **lookup** gate (Moza rights) and a **map**
annotation (Helios routing).

So "add a new VT" breaks into three honest cases:

| The new workflow needs… | Cost |
|---|---|
| Only existing primitives (fetch + lookup/map + model + an existing target) | **Add one `workflows/<vt>/` folder. Zero platform code.** |
| A brand-new external target (e.g. a chat tool) | One adapter class in `adapters/external.py` + one line in the CLI registry. |
| A genuinely new step behaviour | A small handler in `core/runner.py`, shared by every later workflow. |

That escalation is the platform value: the common case is configuration; the
rare case is a small, *shared* addition — never a rebuild.

Record a new workflow's model fixtures once with the same config-driven tool:

```bash
OPENROUTER_API_KEY=... python3 tools/record_fixtures.py \
    --config workflows/<vt>/config.json
```

## 9. What is mocked, and what that hides

| Real thing | Mock |
|---|---|
| Song / candidate source | per-workflow `fixtures/*.json` |
| Rights registry | `fixtures/rights.json` (a lookup with one deliberate `unknown`) |
| The model | `fixtures/model_responses.json`, recorded from a real model |
| Review system / ATS | local SQLite (`state/review_system.db`, `state/ats.db`) behind the adapter |

Model fixtures are recorded from a real model (`deepseek/deepseek-v4-pro-0813`
via OpenRouter, stdlib `urllib`, no SDK). The model judges each item's metadata
substituted into the prompt's `{item_json}` placeholder. Recording from a real
model (not hand-writing) gives realistic imperfection: e.g. Moza's `song_050`
("Simple Fugue Sketch") returns `needs_review` — *"Fugue implies polyphonic
complexity despite 'simple'; unclear without audio"* — a genuine borderline case
a person wouldn't invent. `LiveModel` is a stub marking where a real, un-replayed
call would plug in.

The adapter boundary (`execute` / `reconcile`) is where a real system replaces
the mock — swapping the target is a new adapter plus config, never a runner
change.

## 10. Known limitations

- **Single process.** This demonstrates safe *sequential* recovery. Two
  concurrent workers could each `reconcile`, both get "no", and both create.
  Fixing that needs a lock or native idempotency at the target — out of scope.
- **Reconcile depends on lookup-by-key.** It only works if the target can answer
  "did action K happen?". Production should prefer a target that accepts an
  idempotency key **natively** so the *system* dedupes; lookup-and-reconcile is
  the fallback.
- **Halts don't yet resume-and-continue.** A run stops cleanly on unknown
  rights, a bad model verdict, or an unreachable target — but the *exit* paths
  are one-directional: fixing the cause and retrying the same run may skip the
  halted item or re-stop, and a `pending` approval has no "approve then execute"
  path (the gate blocks, but nothing opens it). Making these step states
  non-terminal — re-attempt on retry, block completion while halted, add an
  `approve` command — is the next iteration of the state machine.
- **`--approval auto` is a demo-only override.** It lets `make demo-helios` show
  a note actually created even though the config declares `required`. In
  production, authorization must live in the platform, not a flag an operator can
  flip.
- **State is local SQLite** under `state/`.
- **The external mock hides real semantics.** A local mock proves the recovery
  logic, not that a specific task tracker or ATS behaves this way.

## 11. Deliberate choices worth calling out

- **JSON config, not YAML** — sanctioned by the build brief; keeps the runtime at
  **zero dependencies** so the clean-clone / no-key gate has nothing to install.
- **`core/` instead of `platform/`** — avoids shadowing the stdlib `platform`
  module (see §3).
- **`fetch` orders the unknown-rights song last** — one Moza fixture set then
  shows both "one task per include" and "unknown stops the run" in one demo.
- **Crash demos use a single-include-song source** — so "one task, never two"
  reads cleanly; the full workflow runs under `make demo`.
- **No `web/`, `scheduler/`, `event_bus/`, layered `domain/…/infrastructure/`.**
  Each would need a justification that doesn't exist yet at this size; the brief
  rewards restraint. Absence here is a decision, not an omission.
