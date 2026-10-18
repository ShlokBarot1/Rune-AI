"""Claude (Anthropic API) provider with spend guardrails. Used by core.llm for the teams in RUNE_CLAUDE_FOR.

Guardrails:
  * CLAUDE_MAX_TOKENS  (default 2048)    hard cap on output tokens per call
  * CLAUDE_DAILY_TOKENS (default 200000) input+output tokens per UTC day; over it, calls fall
    through to the free providers instead of spending more
  * effort "low": the agents' calls are short JSON / summaries, low effort keeps thinking small
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import anthropic

from . import bus

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")


class BudgetExceeded(RuntimeError):
    pass


def enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(timeout=120.0, max_retries=2)


def _db():
    con = bus.connect()
    con.execute("create table if not exists llm_usage (day text, provider text, input integer, output integer)")
    return con


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def used_today() -> int:
    with _db() as con:
        row = con.execute("select coalesce(sum(input + output), 0) from llm_usage where day=? and provider='claude'",
                          (_today(),)).fetchone()
    return int(row[0])


def _record(usage) -> None:
    with _db() as con:
        con.execute("insert into llm_usage values (?,?,?,?)", (_today(), "claude", usage.input_tokens, usage.output_tokens))


def _content(user: str, images: Optional[list[str]]) -> list[dict]:
    """images are data URLs (core.llm.image_url) or http(s) URLs."""
    blocks: list[dict] = []
    for img in images or []:
        if img.startswith("data:"):
            mime, data = img[5:].split(";base64,", 1)
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        else:
            blocks.append({"type": "image", "source": {"type": "url", "url": img}})
    blocks.append({"type": "text", "text": user})
    return blocks


def chat(system: str, user: str, images: Optional[list[str]] = None) -> str:
    budget = int(os.getenv("CLAUDE_DAILY_TOKENS", "200000"))
    if used_today() >= budget:
        raise BudgetExceeded(f"Claude daily budget of {budget} tokens used")

    response = _client().beta.messages.create(
        model=MODEL,
        max_tokens=int(os.getenv("CLAUDE_MAX_TOKENS", "2048")),
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": _content(user, images)}],
    )
    _record(response.usage)
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined: {response.stop_details}")
    return "".join(b.text for b in response.content if b.type == "text")
