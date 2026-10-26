"""Claude via the Claude Code CLI (`claude -p`), logged into the user's Claude plan — no API credits.

Runs headless on this machine with no tools, no MCP servers, no settings and no saved session, so each
call is a plain prompt -> answer. Used by core.llm for the teams in RUNE_CLAUDE_FOR.

Guardrails (protect the plan's usage for the user's own Claude Code work):
  * CLAUDE_DAILY_TOKENS (default 150000) input+output tokens per UTC day; over it, calls fall through
    to the free providers
  * CLAUDE_MAX_TOKENS (default 2048) output cap per call
  * --effort low: the agents' calls are short JSON / summaries
  * when the plan's own usage limit is hit, the CLI errors and the chain falls through as well
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Optional

from . import bus

MODEL = os.getenv("CLAUDE_MODEL", "sonnet")


class BudgetExceeded(RuntimeError):
    pass


def enabled() -> bool:
    return os.getenv("RUNE_USE_CLAUDE", "1") == "1" and shutil.which("claude") is not None


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


def _record(input_tokens: int, output_tokens: int) -> None:
    with _db() as con:
        con.execute("insert into llm_usage values (?,?,?,?)", (_today(), "claude", input_tokens, output_tokens))


def _image_files(images: list[str]) -> tuple[list[str], list[str]]:
    """(paths Claude can open with its Read tool, temp files to delete): local files as they are, data: URLs and
    web images written to temp files."""
    import base64
    import httpx
    paths, temps = [], []
    for img in images:
        if img.startswith("data:"):
            head, b64 = img.split(",", 1)
            ext = "." + (head.split("/")[1].split(";")[0] if "/" in head else "png")
            raw = base64.b64decode(b64)
        elif img.startswith("http"):
            raw, ext = httpx.get(img, timeout=60, follow_redirects=True).content, ".jpg"
        else:
            paths.append(os.path.abspath(img))
            continue
        with tempfile.NamedTemporaryFile("wb", suffix=ext, delete=False) as t:
            t.write(raw)
        paths.append(t.name)
        temps.append(t.name)
    return paths, temps


def chat(system: str, user: str, images: Optional[list[str]] = None) -> str:
    """images: the CLI looks at them with its Read tool (only that tool, only for those files)."""
    budget = int(os.getenv("CLAUDE_DAILY_TOKENS", "150000"))
    if used_today() >= budget:
        raise BudgetExceeded(f"Claude daily budget of {budget} tokens used")

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(system)  # system prompts with skills are long; a file avoids Windows command-line limits
    env = {**os.environ, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": os.getenv("CLAUDE_MAX_TOKENS", "2048")}
    env.pop("ANTHROPIC_API_KEY", None)  # never bill API credits; use the logged-in plan
    paths, temps = _image_files(images or [])
    tools = ["--tools", "Read", "--allowedTools", "Read"] if paths else ["--tools", ""]
    if paths:
        user = "First open and look at these image files with the Read tool:\n" + "\n".join(paths) + "\n\n" + user
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", MODEL, "--effort", "low", "--system-prompt-file", f.name, *tools,
             "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence", "--output-format", "json"],
            input=user, capture_output=True, text=True, encoding="utf-8", timeout=240 if paths else 180, env=env,
            cwd=tempfile.gettempdir())
    finally:
        os.unlink(f.name)
        for t in temps:
            os.unlink(t)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[:200]}")
    usage = out.get("usage") or {}
    # cached prompt reads cost ~10% of fresh input, so they count at 10% toward the daily cap
    _record(usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0) // 10, usage.get("output_tokens", 0))
    if out.get("is_error"):
        raise RuntimeError(f"claude: {out.get('result') or out.get('subtype')}")
    return out.get("result", "")
