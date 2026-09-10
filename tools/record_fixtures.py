"""Record model fixtures from a real OpenRouter call. Run ONCE, offline of
the demo. The demo itself never needs a key -- it replays this file.

Usage:
    OPENROUTER_API_KEY=sk-... python3 tools/record_fixtures.py

Uses only the standard library (urllib) -- no SDK, no new dependency.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

MODEL = "google/gemma-4-31b-it:free"  # pick any current free model (see openrouter.ai/models?variant=free)
URL = "https://openrouter.ai/api/v1/chat/completions"
OUTCOMES = ["include", "exclude", "needs_review"]

# Free models are heavily rate-limited (HTTP 429). Retry with backoff and
# pace requests so a full recording run completes on the free tier.
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
                data = json.load(resp)
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_SECONDS)
                continue
            raise


def parse(content):
    start, end = content.find("{"), content.rfind("}")
    obj = json.loads(content[start:end + 1])
    verdict = obj["verdict"].strip()
    if verdict not in OUTCOMES:
        raise ValueError(f"model returned verdict {verdict!r} not in {OUTCOMES}")
    return {"verdict": verdict, "reason": obj.get("reason", "")[:80]}


OUT_PATH = "fixtures/model_responses.json"


def _load_existing():
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH) as f:
            return json.load(f)
    return {}


def _save(out):
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)


def main():
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Set OPENROUTER_API_KEY", file=sys.stderr)
        return 1
    with open("fixtures/songs.json") as f:
        songs = json.load(f)
    with open("prompts/teaching_suitability.txt") as f:
        template = f.read()
    # Accumulate: keep anything already recorded so bursts add up across runs
    # (free-tier rate limits often force several passes).
    out = _load_existing()
    for song in songs:
        sid = song["song_id"]
        if sid in out:
            print(f"skip {sid} (already recorded: {out[sid]['verdict']})")
            continue
        prompt = template.replace("{song_json}", json.dumps(song))
        try:
            out[sid] = parse(call(api_key, prompt))
            _save(out)  # persist immediately so progress is never lost
            print(f"recorded {sid}: {out[sid]['verdict']}")
        except Exception as e:  # noqa: BLE001 - one-off tool, surface and continue
            print(f"FAILED {sid}: {e}", file=sys.stderr)
    print(f"{OUT_PATH}: {len(out)}/{len(songs)} songs recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
