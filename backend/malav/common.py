"""Small helpers shared by Malav's agents."""
from __future__ import annotations

import json
import os
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def fixture(name: str, cid: str = ""):
    """A fixture; with cid, every client_id in it becomes this run's (else logs, Meta objects and insights of
    separate demo runs would all land on the fixture's client)."""
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))

    def own(x):
        if isinstance(x, dict):
            return {k: (cid if k == "client_id" else own(v)) for k, v in x.items()}
        return [own(v) for v in x] if isinstance(x, list) else x
    return own(data) if cid else data


def demo_mode(state: dict) -> bool:
    """RUNE_OFFLINE=1, or a run started without any client input: every agent uses the Brewbloom fixtures.
    This keeps CI (which starts the graph with only a client_id and has no LLM) on the fixture path."""
    i = state.get("intake") or {}
    return os.getenv("RUNE_OFFLINE") == "1" or not (i.get("website") or i.get("url") or i.get("notes") or i.get("creatives"))
