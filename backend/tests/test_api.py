"""The dashboard's path through the API: approve every gate, go live, run a cycle, decide an action."""
import os
import time


def test_api_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNE_AUTO_APPROVE", "0")
    monkeypatch.setenv("RUNE_CHECKPOINTS", str(tmp_path / "cp.db"))
    from fastapi.testclient import TestClient

    import api
    c = TestClient(api.app)

    def settle(cid):
        for _ in range(200):
            if not c.get(f"/runs/{cid}").json()["running"]:
                return
            time.sleep(0.05)
        raise AssertionError("pipeline did not settle")

    cid = c.post("/runs", json={"client_id": "demo_brewbloom"}).json()["client_id"]
    gates = []
    for _ in range(30):
        settle(cid)
        pending = c.get("/approvals").json()
        if not pending:
            break
        gates.append(pending[0].get("gate") or pending[0]["type"])
        assert c.post(f"/runs/{cid}/resume", json={"action": "approve"}).status_code == 200
    assert gates[-1] == "go_live" and "budget" in gates and "targeting" in gates
    assert c.get("/runs").json()[0]["live"] is True

    out = c.post(f"/runs/{cid}/cycle", json={"source": "simulated"}).json()
    assert out["report"] and out["stats"]
    perf = c.get(f"/runs/{cid}/performance").json()
    assert perf["daily"] and perf["totals"]["spend"] > 0
    todo = [a for a in perf["actions"] if a["risk"] == "approval" and a["status"] == "proposed"]
    if todo:
        assert c.post(f"/runs/{cid}/actions/{todo[0]['id']}", json={"approve": True}).status_code == 200
