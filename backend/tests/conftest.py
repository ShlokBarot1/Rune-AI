import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test: fresh DB, no LLM, no Meta calls."""
    monkeypatch.setenv("RUNE_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("META_DRY_RUN", "1")
    monkeypatch.setattr("auditor.agent.llm_review", lambda state: [])

    def no_llm(*a, **k):
        raise ConnectionError("no LLM in tests")
    monkeypatch.setattr("core.llm.chat", no_llm)
