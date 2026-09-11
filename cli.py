"""Command-line entry point. Wires the reusable core to concrete adapters
and a workflow config, then runs or inspects.

    python3 cli.py run --config workflows/<vt>/config.json [flags]
    python3 cli.py inspect
"""
import argparse
import os
import sys

from core.runner import load_config, Runner
from core.state import Store
from core.observability import render
from adapters.external import ReviewSystemAdapter, AtsAdapter
from adapters.model import ReplayModel, LiveModel


def _paths(state_dir):
    os.makedirs(state_dir, exist_ok=True)
    return {
        "runtime": os.path.join(state_dir, "runtime.db"),
        "review_system": os.path.join(state_dir, "review_system.db"),
        "ats": os.path.join(state_dir, "ats.db"),
    }


def _build_adapters(paths, down):
    # registry keyed by config `target`; adding a target = one line here
    return {
        "review_system": ReviewSystemAdapter(paths["review_system"], down=down),
        "ats": AtsAdapter(paths["ats"], down=down),
    }


def _build_model(cfg, live):
    if live:
        return LiveModel()
    for step in cfg["steps"]:
        if step["type"] == "model":
            return ReplayModel(step["responses"])
    return None  # no model step in this workflow


def cmd_run(args):
    cfg = load_config(args.config)
    if args.source:  # override the fetch source (e.g. to demo a missing id)
        cfg["steps"][0]["source"] = os.path.abspath(args.source)
    paths = _paths(args.state_dir)
    store = Store(paths["runtime"])
    adapters = _build_adapters(paths, down=(args.external == "down"))
    model = _build_model(cfg, args.live)
    # the CLI can only TIGHTEN the gate (force approval on), never loosen a
    # config that declares `required` -- authorization is not operator-flippable
    approval_override = "required" if args.require_approval else None
    runner = Runner(cfg, store, adapters, model,
                    crash_at=args.crash_at, approval_override=approval_override)
    print(f"RUN {args.run_id}   workflow: {cfg['vt']}/{cfg['workflow']}")
    status = runner.run(args.run_id)
    store.close()
    return 0 if status in ("completed", "stopped") else 1


def cmd_inspect(args):
    paths = _paths(args.state_dir)
    store = Store(paths["runtime"])
    print(render(store))
    store.close()
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="vt-runtime")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="execute a workflow config")
    r.add_argument("--config", required=True)
    # required on purpose: the run_id is the trigger-instance id; the platform
    # refuses to invent identity (a generated default would duplicate on retry)
    r.add_argument("--run-id", required=True)
    r.add_argument("--crash-at", choices=["A", "B"], default=None)
    # tightening-only: forces the approval gate ON (the announced change
    # request). There is deliberately no flag to turn a `required` gate off.
    r.add_argument("--require-approval", action="store_true")
    r.add_argument("--external", choices=["up", "down"], default="up")
    r.add_argument("--state-dir", default="state")
    r.add_argument("--source", default=None, help="override the fetch source")
    r.add_argument("--live", action="store_true")
    r.set_defaults(func=cmd_run)

    i = sub.add_parser("inspect", help="print all runs, steps, external actions")
    i.add_argument("--state-dir", default="state")
    i.set_defaults(func=cmd_inspect)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
