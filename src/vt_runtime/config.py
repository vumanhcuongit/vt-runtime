"""Workflow config loader.

JSON, not YAML -- one fewer dependency so the clean-clone gate needs zero
installs. A different workflow is a different config file; the runner
holds no domain knowledge.
"""
import json


def load_config(path: str) -> dict:
    with open(path) as f:
        return json.load(f)
