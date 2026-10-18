"""Single LLM client for every agent. Talks to FreeLLMAPI (OpenAI-compatible).

Agents ask for a role alias, never a concrete model:
  reason   -> strategy, budget rationale, auditor, suggestion evaluator
  fast     -> extraction, summarising
  creative -> hooks and copy
  vision   -> creative QA, competitor ad analysis
Override any alias with env RUNE_MODEL_<ALIAS>, e.g. RUNE_MODEL_VISION=gemini-2.5-flash.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

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


def chat(alias: str, system: str, user: str, images: Optional[list[str]] = None, **kwargs) -> str:
    content = user if not images else [
        {"type": "text", "text": user},
        *({"type": "image_url", "image_url": {"url": image_url(i)}} for i in images),
    ]
    resp = client().chat.completions.create(
        model=model_for(alias),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
        **kwargs,
    )
    return resp.choices[0].message.content or ""


def _json_block(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def structured(alias: str, system: str, user: str, schema: type[T],
               images: Optional[list[str]] = None, retries: int = 2) -> T:
    """Chat call that must return `schema`. Feeds validation errors back to the model."""
    system = (f"{system}\n\nReply with ONLY one JSON object (no prose, no code fences) "
              f"matching this JSON schema:\n{json.dumps(schema.model_json_schema())}")
    prompt = user
    for attempt in range(retries + 1):
        raw = chat(alias, system, prompt, images)
        try:
            return schema.model_validate_json(_json_block(raw))
        except ValidationError as e:
            if attempt == retries:
                raise
            prompt = f"{user}\n\nYour previous reply was invalid.\nReply:\n{raw}\n\nErrors:\n{e}\n\nReturn corrected JSON only."
    raise AssertionError("unreachable")
