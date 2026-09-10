import argparse
import os
from datetime import datetime, timezone

from .config import load_config
from .state import Store
from .adapter import ReviewSystemAdapter
from .model import ReplayModel, LiveModel
from .runner import Runner
from .inspect import render

RESPONSES = "fixtures/model_responses.json"


def _default_run_id():
    return "run_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _paths(state_dir):
    os.makedirs(state_dir, exist_ok=True)
    return (os.path.join(state_dir, "runtime.db"),
            os.path.join(state_dir, "review_system.db"))


def cmd_run(args):
    cfg = load_config(args.config)
    runtime_db, review_db = _paths(args.state_dir)
    store = Store(runtime_db)
    adapter = ReviewSystemAdapter(review_db, down=(args.external == "down"))
    model = LiveModel() if args.live else ReplayModel(RESPONSES)
    runner = Runner(cfg, store, adapter, model,
                    crash_at=args.crash_at, approval_override=args.approval)
    run_id = args.run_id or _default_run_id()
    print(f"RUN {run_id}   workflow: {cfg['vt']}/{cfg['workflow']}")
    status = runner.run(run_id)
    store.close()
    return 0 if status in ("completed", "stopped") else 1


def cmd_inspect(args):
    runtime_db, _ = _paths(args.state_dir)
    store = Store(runtime_db)
    print(render(store))
    store.close()
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="vt_runtime")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="execute a workflow config")
    r.add_argument("--config", required=True)
    r.add_argument("--run-id", default=None)
    r.add_argument("--crash-at", choices=["A", "B"], default=None)
    r.add_argument("--approval", choices=["auto", "required"], default=None)
    r.add_argument("--external", choices=["up", "down"], default="up")
    r.add_argument("--state-dir", default="state")
    r.add_argument("--live", action="store_true")
    r.set_defaults(func=cmd_run)

    i = sub.add_parser("inspect", help="print all runs, steps, external actions")
    i.add_argument("--state-dir", default="state")
    i.set_defaults(func=cmd_inspect)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)
