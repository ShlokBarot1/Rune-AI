"""Provider chain: Claude only for allowed teams and only under budget; failures fall through."""
import core.llm as llm
from core import claude


def test_chain_order_and_budget(monkeypatch):
    monkeypatch.undo()  # this test needs the real llm.chat, not the conftest stub
    monkeypatch.setenv("RUNE_DB", str(__import__("tempfile").mkdtemp()) + "/llm.db")
    monkeypatch.setattr(claude, "enabled", lambda: True)
    monkeypatch.setenv("FREELLM_API_KEY", "freellmapi-test")
    monkeypatch.setenv("CLAUDE_DAILY_TOKENS", "100")
    monkeypatch.setenv("RUNE_CLAUDE_FOR", "shlok,auditor,core")

    names = lambda agent: [n for n, _ in llm.providers("reason", agent)]
    assert names("shlok.budget") == ["claude", "freellm", "ollama"]
    assert names("malav.hooks") == ["freellm", "ollama"]
    assert llm._caller() == __name__  # no agent= : the calling module decides the team

    # Claude over budget -> BudgetExceeded -> next provider answers
    claude._record(80, 30)
    monkeypatch.setattr(llm, "_openai_chat", lambda c, model, s, u, i: f"free:{model}")
    assert llm.chat("reason", "sys", "hi", agent="shlok.budget").startswith("free:")
