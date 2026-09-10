"""Record model fixtures for a workflow from a real model via OpenRouter.

Run ONCE per workflow, offline of the demo. The demo replays the recorded
file and needs no key. Config-driven, so the same tool records fixtures
for any workflow that has a model step:

    OPENROUTER_API_KEY=... python3 tools/record_fixtures.py \
        --config workflows/moza_song_screening/config.json

Uses only the standard library (urllib) -- no SDK, no dependency. Free
models are heavily rate-limited (HTTP 429); a paid model records in one
pass. Progress is saved after each item and already-recorded items are
skipped, so a run is resumable.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# repo root on path so we can reuse the real config loader (path resolution)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.runner import load_config  # noqa: E402

MODEL = "deepseek/deepseek-v4-pro-0813"  # recorded once; the demo replays it
URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_RETRIES = 12
BACKOFF_SECONDS = 6


def call(api_key, prompt):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        URL, data=body,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
    )
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_SECONDS)
                continue
            raise


def parse(content, outcomes):
    start, end = content.find("{"), content.rfind("}")
    obj = json.loads(content[start:end + 1])
    verdict = obj["verdict"].strip()
    if verdict not in outcomes:
        raise ValueError(f"model returned verdict {verdict!r} not in {outcomes}")
    return {"verdict": verdict, "reason": obj.get("reason", "")[:80]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Set OPENROUTER_API_KEY", file=sys.stderr)
        return 1

    cfg = load_config(args.config)          # resolves paths to absolute
    item_id_field = cfg["item_id_field"]
    fetch_step = cfg["steps"][0]
    model_step = next(s for s in cfg["steps"] if s["type"] == "model")

    with open(fetch_step["source"]) as f:
        items = json.load(f)
    with open(model_step["prompt"]) as f:
        template = f.read()
    out_path = model_step["responses"]
    out = json.load(open(out_path)) if os.path.exists(out_path) else {}

    for item in items:
        iid = item[item_id_field]
        if iid in out:
            print(f"skip {iid} (already recorded: {out[iid]['verdict']})")
            continue
        prompt = template.replace("{item_json}", json.dumps(item))
        try:
            out[iid] = parse(call(api_key, prompt), model_step["outcomes"])
            json.dump(out, open(out_path, "w"), indent=2)
            print(f"recorded {iid}: {out[iid]['verdict']}")
        except Exception as e:  # noqa: BLE001 - one-off tool, surface and continue
            print(f"FAILED {iid}: {e}", file=sys.stderr)
    print(f"{out_path}: {len(out)}/{len(items)} items recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
