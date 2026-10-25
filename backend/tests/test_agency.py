"""Agency mode (RUNE_AUTH=1): sign-in, roles, a client's view, white-label report, share links."""
import time


def test_roles_portal_report_and_share(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNE_AUTH", "1")
    monkeypatch.setenv("RUNE_AUTO_APPROVE", "1")
    monkeypatch.setenv("RUNE_CHECKPOINTS", str(tmp_path / "cp.db"))
    from fastapi.testclient import TestClient

    import api
    c = TestClient(api.app)
    assert c.get("/runs").status_code == 401
    assert c.get("/auth/status").json()["setup_needed"]
    owner = {"Authorization": f"Bearer {c.post('/auth/setup', json={'email': 'o@agency.in', 'name': 'O', 'role': 'owner', 'password': 'owner-pass-1'}).json()['token']}"}
    assert c.post("/auth/setup", json={"email": "x@y.z", "name": "X", "role": "owner", "password": "12345678"}).status_code == 409

    for cid in ("acme", "other"):
        assert c.post("/runs", json={"client_id": cid}, headers=owner).status_code == 200
    for _ in range(400):
        if all(c.get(f"/runs/{x}", headers=owner).json()["values"].get("live") for x in ("acme", "other")):
            break
        time.sleep(0.05)
    c.post("/runs/acme/cycle", json={"source": "simulated"}, headers=owner)

    assert c.post("/auth/users", headers=owner, json={"email": "c@acme.in", "name": "Acme", "role": "client",
                                                       "password": "client-pass-1", "clients": ["acme"]}).status_code == 200
    client = {"Authorization": f"Bearer {c.post('/auth/login', json={'email': 'c@acme.in', 'password': 'client-pass-1'}).json()['token']}"}
    assert c.post("/auth/login", json={"email": "c@acme.in", "password": "wrong"}).status_code == 401

    assert [r["client_id"] for r in c.get("/runs", headers=client).json()] == ["acme"]  # only theirs
    assert c.get("/runs/other", headers=client).status_code == 403
    assert c.post("/runs", json={}, headers=client).status_code == 403             # can't start campaigns
    assert c.post("/runs/acme/stop", headers=client).status_code == 403            # or stop agents
    assert c.post("/auth/users", json={}, headers=client).status_code == 403
    assert c.get("/events", headers=client).status_code == 403                    # not the agency-wide feed

    c.post("/settings", headers=owner, json={"agency_name": "Bright Ads", "color": "#FF5A1F"})
    page = c.get("/runs/acme/report.html", headers=client)
    assert page.status_code == 200 and "Bright Ads" in page.text and "Daily spend" in page.text
    assert "cr_ad_" not in page.text and "as_wfh" not in page.text  # a client never sees internal ids
    path = c.post("/runs/acme/share", headers=client).json()["path"]
    assert c.get(path).status_code == 200                                          # no sign-in needed
    assert c.get(path[:-2] + "xx").status_code == 404                             # tampered link
