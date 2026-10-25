"""Client-supplied creatives: no generation anywhere, one ad per upload, odd shapes letterboxed, live uploads."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from core.schemas import CreativeFeedback, CreativePackage
from malav import creatives, hooks, media, own, strategy
from malav import feedback as loops

FIX = Path(__file__).resolve().parent.parent / "fixtures"
load = lambda n: json.loads((FIX / f"{n}.json").read_text(encoding="utf-8"))
CID = "test_own_creatives"


@pytest.fixture
def uploads():
    d = media.MEDIA / CID / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1080, 1350), "red").save(d / "feed.png")
    Image.new("RGB", (1000, 700), "blue").save(d / "odd.jpg")  # 10:7 is no Meta ratio: letterboxed to 1.91:1
    subprocess.run([media.ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=green:s=720x1280:d=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "reel.mp4")], check=True)
    items = [{"path": f"media/{CID}/uploads/feed.png", "headline": "Our own headline"},
             {"path": f"media/{CID}/uploads/odd.jpg"}, {"path": f"media/{CID}/uploads/reel.mp4"},
             {"path": "../.env"}, {"path": "fixtures/media/placeholder.png"}]  # outside the run's uploads: ignored
    yield items
    shutil.rmtree(media.MEDIA / CID, ignore_errors=True)


def test_client_creatives_run_without_generation(uploads, monkeypatch):
    monkeypatch.setattr(creatives, "produce", lambda *a, **k: pytest.fail("generated media in a client-creatives run"))
    brief = load("brief") | {"client_id": CID}
    state = {"client_id": CID, "intake": {"website": "https://brewbloom.in", "creatives": uploads}, "brief": brief,
             "research": load("research") | {"client_id": CID}, "context_doc": ""}

    assert len(own.uploads(state)) == 3
    assets = own.probe_all(CID, own.uploads(state))
    assert [(a.kind, a.ratio) for a in assets] == [("image", "4:5"), ("image", "1.91:1"), ("video", "9:16")]
    assert assets[1].path.endswith("odd_1_91x1.png") and (assets[1].width, assets[1].height) == (1200, 628)
    assert assets[2].thumbnail_path and 2.5 <= assets[2].duration_s <= 3.5
    assert own.deliverables(assets) == {"n_images": 2, "n_videos": 1, "ratios": ["4:5", "9:16", "1.91:1"]}

    brief["deliverables"] |= {"n_images": 2, "n_videos": 1, "ratios": ["4:5", "9:16", "1.91:1"]}
    state |= strategy.strategy_step(state)
    ads = [a for c in state["blueprint"]["campaigns"] for s in c["adsets"] for a in s["ads"]]
    assert sorted((a["format"], tuple(a["ratios"])) for a in ads) == [
        ("image", ("1.91:1",)), ("image", ("4:5",)), ("video", ("9:16",))]

    state |= hooks.hooks_step(state)
    state |= creatives.creatives_step(state)
    pkg = CreativePackage.model_validate(state["package"])
    assert len(pkg.creatives) == 3 and all(own.is_own(c) and c.qa.passed for c in pkg.creatives)
    assert {c.path for c in pkg.creatives} == {a.path for a in assets}
    assert next(c for c in pkg.creatives if c.path.endswith("feed.png")).ad_copy.headline == "Our own headline"

    from auditor.agent import deterministic_checks
    state |= {"budget_plan": {"client_id": CID, "currency": "INR", "total": 150000, "days": 30, "mode": "ABO",
                              "bid_strategy": "LOWEST_COST_WITHOUT_CAP", "testing_reserve": 0,
                              "campaign_daily": {}, "adset_daily": {s.ref: 1000 for s in pkg.blueprint.adsets()}},
              "targeting": [{"adset_ref": s.ref, "spec": {}, "summary": ""} for s in pkg.blueprint.adsets()]}
    found = [f for f in deterministic_checks(state) if f.dimension in ("creative", "brief", "strategy")]
    assert not found, found

    fb = CreativeFeedback.model_validate(load("creative_feedback") | {"client_id": CID,
                                         "adset_ref": pkg.creatives[0].adset_ref})
    assert loops.handle_feedback(state, fb).creatives == []  # asks the client instead of generating
    assert loops.apply_fixes(state, []) == {}


def test_live_client_upload_becomes_a_pending_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNE_AUTO_APPROVE", "1")
    monkeypatch.setenv("RUNE_CHECKPOINTS", str(tmp_path / "cp.db"))
    from fastapi.testclient import TestClient

    import api
    c = TestClient(api.app)
    cid = "test_live_upload"
    try:
        c.post("/runs", json={"client_id": cid})
        for _ in range(400):
            if c.get(f"/runs/{cid}").json()["values"].get("live"):
                break
            __import__("time").sleep(0.05)
        r = c.post(f"/malav/runs/{cid}/upload?filename=new ad.png", content=_png(1080, 1920))
        assert r.status_code == 200, r.text
        assert c.post(f"/malav/runs/{cid}/upload?filename=x.exe", content=b"MZ").status_code == 415
        adset = c.get(f"/runs/{cid}").json()["values"]["package"]["blueprint"]["campaigns"][0]["adsets"][0]["ref"]
        r = c.post(f"/malav/runs/{cid}/creatives", json={"adset_ref": adset, "creatives": [{"path": r.json()["path"]}]})
        assert r.status_code == 200, r.text
        action = r.json()["action"]
        assert action["kind"] == "launch_creatives"
        pkg = c.get(f"/runs/{cid}").json()["values"]["package"]
        new = [x for x in pkg["creatives"] if x["creative_id"] in action["change"]["creative_ids"]]
        assert new and new[0]["ratio"] == "9:16" and new[0]["model_used"] == own.SOURCE
        stats = c.post(f"/runs/{cid}/cycle", json={"source": "simulated"}).json()["stats"]
        assert new[0]["creative_id"] not in stats  # paused until approved: no delivery, no spend
        assert c.post(f"/runs/{cid}/actions/{action['id']}", json={"approve": True}).status_code == 200
        stats = c.post(f"/runs/{cid}/cycle", json={"source": "simulated", "day": 7}).json()["stats"]
        assert new[0]["creative_id"] in stats
    finally:
        shutil.rmtree(media.MEDIA / cid, ignore_errors=True)


def _png(w, h) -> bytes:
    from io import BytesIO
    b = BytesIO()
    Image.new("RGB", (w, h), "white").save(b, "PNG")
    return b.getvalue()
