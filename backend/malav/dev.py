"""Run Malav's intake against real keys in the terminal, answering its questions by hand.

    cd backend
    ../.venv/Scripts/python -m malav.dev https://some-shop.com "optional notes: 50k INR, November, sales"
"""
from __future__ import annotations

import sys

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from core import bus
from core.state import RuneState
from malav import intake


def main(url: str, notes: str = "", client_id: str = "dev_client") -> None:
    g = StateGraph(RuneState)
    g.add_node("intake", intake.graph)
    g.add_edge(START, "intake")
    g.add_edge("intake", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": client_id}}

    out = app.invoke({"client_id": client_id, "intake": {"website": url, "notes": notes}}, cfg)
    while out.get("__interrupt__"):
        q = out["__interrupt__"][0].value
        opts = q.get("options") or []
        print(f"\n? {q['question']}" + (f"\n  options: {' | '.join(opts)}  (enter = {opts[0]})" if opts else ""))
        ans = input("> ").strip() or (opts[0] if opts else "")
        out = app.invoke(Command(resume=ans), cfg)

    print("\n--- agent log ---")
    for m in bus.history(client_id):
        if m["type"] == "LOG" and m["sender"] == "malav.intake":
            print("·", m["payload"]["text"])
    print("\n--- brief ---\n", out["brief"], "\n\n--- context doc ---\n", out["context_doc"])


if __name__ == "__main__":
    main(*sys.argv[1:])
