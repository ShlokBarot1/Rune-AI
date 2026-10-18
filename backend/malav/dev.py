"""Run Malav's agents against real keys in the terminal, answering intake questions by hand.

    cd backend
    ../.venv/Scripts/python -m malav.dev https://some-shop.com "optional notes: 50k INR, November, sales"
    ../.venv/Scripts/python -m malav.dev https://some-shop.com "notes" --all    # intake -> ... -> creatives

Gates are skipped here (use the API/dashboard for those); everything lands in media/<client>/.
"""
from __future__ import annotations

import json
import sys

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from core import bus
from core.state import RuneState
from malav import media, pipeline


def main(url: str, notes: str = "", run_all: bool = False, client_id: str = "dev_client") -> None:
    steps = pipeline.STEPS if run_all else pipeline.STEPS[:1]
    g = StateGraph(RuneState)
    for s in steps:
        g.add_node(s.name, s.run)
    names = [s.name for s in steps]
    for a, b in zip([START, *names], [*names, END]):
        g.add_edge(a, b)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": client_id}}

    seen = 0
    out = app.invoke({"client_id": client_id, "intake": {"website": url, "notes": notes}}, cfg)
    while True:
        for m in bus.history(client_id, seen):   # live-ish agent log
            seen = m["seq"]
            if m["type"] in ("LOG", "REQUEST", "RESPONSE") and m["sender"] != "human":
                print(f"· {m['sender']}: {m['payload'].get('text') or m['payload'].get('suggestion') or m['payload']}")
        if not out.get("__interrupt__"):
            break
        q = out["__interrupt__"][0].value
        opts = q.get("options") or []
        print(f"\n? {q['question']}" + (f"\n  options: {' | '.join(opts)}  (enter = {opts[0]})" if opts else ""))
        ans = input("> ").strip() or (opts[0] if opts else "")
        out = app.invoke(Command(resume=ans), cfg)

    keep = {k: out[k] for k in ("brief", "research", "blueprint", "hooks", "package") if k in out}
    path = media.MEDIA / client_id / "dev_outputs.json"   # media/ is gitignored
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(keep, f, indent=2, default=str)
    print(f"\n--- context doc ---\n{out.get('context_doc', '')}\n\nSaved outputs to {path}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--all"]
    main(*args[:2], run_all="--all" in sys.argv)
