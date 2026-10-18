"""Single LLM client for every agent. Talks to FreeLLMAPI (OpenAI-compatible).

Agents ask for a role alias, never a concrete model:
  reason   -> strategy, budget rationale, auditor, suggestion evaluator
  fast     -> extraction, summarising
  creative -> hooks and copy
  vision   -> creative QA, competitor ad analysis
Override any alias with env RUNE_MODEL_<ALIAS>, e.g. RUNE_MODEL_VISION=gemini-2.5-flash.

Provider chain, first that answers wins:
  1. Claude (core/claude.py) — only for agents whose team is in RUNE_CLAUDE_FOR (default "shlok,auditor,core"),
     only while under its daily token budget
  2. FreeLLMAPI — pooled free tiers
  3. Ollama on this machine (OLLAMA_MODEL) — never runs out
Pass agent="shlok.budget" etc. so the chain knows who is calling.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from . import claude

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODELS = {"reason": "auto:smart", "fast": "auto:fast", "creative": "auto", "vision": "auto:smart"}


def model_for(alias: str) -> str:
    return os.getenv(f"RUNE_MODEL_{alias.upper()}", DEFAULT_MODELS.get(alias, alias))


@lru_cache(maxsize=1)
def client() -> OpenAI:
    return OpenAI(
        base_url=os.getenv("FREELLM_BASE_URL", "http://localhost:3001/v1"),
        api_key=os.getenv("FREELLM_API_KEY", "missing"),
        timeout=120,
    )


def image_url(path_or_url: str) -> str:
    """Local file -> data URL so vision models can see it; URLs pass through."""
    if path_or_url.startswith(("http://", "https://", "data:")):
        return path_or_url
    mime = mimetypes.guess_type(path_or_url)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(Path(path_or_url).read_bytes()).decode()}"


@lru_cache(maxsize=1)
def ollama() -> OpenAI:
    return OpenAI(base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"), api_key="ollama", timeout=300)


def _openai_chat(c: OpenAI, model: str, system: str, user: str, images: Optional[list[str]]) -> str:
    content = user if not images else [
        {"type": "text", "text": user},
        *({"type": "image_url", "image_url": {"url": image_url(i)}} for i in images),
    ]
    resp = c.chat.completions.create(
        model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": content}])
    return resp.choices[0].message.content or ""


def providers(alias: str, agent: str) -> list[tuple[str, Callable]]:
    team = agent.split(".")[0]
    chain: list[tuple[str, Callable]] = []
    if claude.enabled() and team in os.getenv("RUNE_CLAUDE_FOR", "shlok,auditor,core").split(","):
        chain.append(("claude", lambda s, u, i: claude.chat(s, u, [image_url(x) for x in i or []])))
    if os.getenv("FREELLM_API_KEY", "").startswith("freellmapi-"):
        chain.append(("freellm", lambda s, u, i: _openai_chat(client(), model_for(alias), s, u, i)))
    chain.append(("ollama", lambda s, u, i: _openai_chat(ollama(), os.getenv("OLLAMA_MODEL", "llama3.1:latest"), s, u, i)))
    return chain


def chat(alias: str, system: str, user: str, images: Optional[list[str]] = None, agent: str = "") -> str:
    errors = []
    for name, call in providers(alias, agent):
        try:
            return call(system, user, images)
        except Exception as e:  # next provider; the last one standing answers
            errors.append(f"{name}: {e.__class__.__name__}: {str(e)[:120]}")
    raise RuntimeError("all LLM providers failed — " + " | ".join(errors))


def _json_block(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def structured(alias: str, system: str, user: str, schema: type[T],
               images: Optional[list[str]] = None, retries: int = 2, agent: str = "") -> T:
    """Chat call that must return `schema`. Feeds validation errors back to the model."""
    system = (f"{system}\n\nReply with ONLY one JSON object (no prose, no code fences) "
              f"matching this JSON schema:\n{json.dumps(schema.model_json_schema())}")
    prompt = user
    for attempt in range(retries + 1):
        raw = chat(alias, system, prompt, images, agent=agent)
        try:
            return schema.model_validate_json(_json_block(raw))
        except ValidationError as e:
            if attempt == retries:
                raise
            prompt = f"{user}\n\nYour previous reply was invalid.\nReply:\n{raw}\n\nErrors:\n{e}\n\nReturn corrected JSON only."
    raise AssertionError("unreachable")
