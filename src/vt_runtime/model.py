"""Model provider abstraction.

ReplayModel replays fixtures recorded from real calls, so the runner
needs no API key at demo time. LiveModel is a deliberate stub: the
interface exists so the "real LLM decision" has a concrete home and can
be wired later without touching the runner.
"""
import json


class ModelProvider:
    def judge(self, item_id: str, item: dict, prompt: str, outcomes: list) -> tuple:
        raise NotImplementedError


class ReplayModel(ModelProvider):
    def __init__(self, responses_path: str):
        with open(responses_path) as f:
            self._responses = json.load(f)

    def judge(self, item_id, item, prompt, outcomes):
        if item_id not in self._responses:
            raise KeyError(f"no recorded model response for {item_id}")
        rec = self._responses[item_id]
        verdict, reason = rec["verdict"], rec["reason"]
        if verdict not in outcomes:
            raise ValueError(
                f"recorded verdict {verdict!r} not in configured outcomes {outcomes}"
            )
        return verdict, reason


class LiveModel(ModelProvider):
    def judge(self, item_id, item, prompt, outcomes):
        raise NotImplementedError(
            "live model not wired; run with replay fixtures (see README)"
        )
