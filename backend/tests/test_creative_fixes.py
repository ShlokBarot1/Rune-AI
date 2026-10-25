"""What the ads show for every kind of client (products, catalogs, own photos, brand scenes); logos are not product
photos; ask before generating; copy findings rewrite copy, not images."""
import json
from pathlib import Path

import pytest
from PIL import Image

from core.schemas import AdCopy, AuditReport, ClientBrief, CreativePackage, Finding, FixPrompt
from malav import creatives, intake, media
from malav import feedback as loops

FIX = Path(__file__).resolve().parent.parent / "fixtures"
load = lambda n: json.loads((FIX / f"{n}.json").read_text(encoding="utf-8"))


def test_logos_and_icons_are_not_product_photos(tmp_path):
    assert not media.product_photo_ok("https://www.nike.com/android-icon-192x192.png")  # by name, no download
    assert not media.product_photo_ok("https://x.com/static/logo.png?v=2")
    Image.new("RGB", (300, 120)).save(tmp_path / "small.png")
    Image.new("RGB", (1000, 1200)).save(tmp_path / "shoe.jpg")
    assert not media.product_photo_ok(str(tmp_path / "small.png"))
    assert media.product_photo_ok(str(tmp_path / "shoe.jpg"))


def test_what_the_ads_show_covers_every_kind_of_client(tmp_path):
    shoe, room = tmp_path / "shoe.jpg", tmp_path / "salon.jpg"
    for f in (shoe, room):
        Image.new("RGB", (1000, 1200)).save(f)
    two = [intake.Product(name="Air Max"), intake.Product(name="Jordan 1")]

    # unsure (service, app, local business): one question, three ways; the first needs no file
    d = intake.Draft(business_name="Glow Salon")
    q, options, upload = intake.visual_question(d)
    assert options[0] == intake.BRAND_OPT and intake.RANGE_OPT in options and len(options) == 4 and not upload
    assert "visual" in intake.missing(d)

    # brand / category launch: typed or picked, nothing else needed
    for a in (intake.BRAND_OPT, "make it general, about the category"):
        assert intake.apply_visual(d, intake.visual_patch(d, a)).creative_focus == "brand"

    # own photos: picking it isn't enough, the photos are the answer that completes it
    d = intake.apply_visual(d, intake.visual_patch(d, intake.PHOTOS_OPT))
    assert d.creative_focus == "photos" and not intake.visual_ready(d)
    assert intake.visual_question(d)[2] == "Upload photos"
    d = intake.apply_visual(d, intake.visual_patch(d, str(room)))
    assert d.photos == [str(room)] and intake.visual_ready(d) and "visual" not in intake.missing(d)

    # catalog: one photo per product, in order; a logo is refused
    d = intake.Draft(business_name="Nike", products=two, creative_focus="product")
    assert intake.visual_patch(d, "https://www.nike.com/android-icon-192x192.png") is None
    d = intake.apply_visual(d, intake.visual_patch(d, f"{shoe}\n{room}"))
    assert [p.images for p in d.products] == [[str(shoe)], [str(room)]] and intake.visual_ready(d)
    assert intake.visual_patch(d, "hmm, not sure") is None  # neither an option nor a photo: asked again


def test_creatives_ask_before_generating_and_remember(monkeypatch, tmp_path):
    brief = ClientBrief.model_validate(load("brief"))
    brief.products[0].images = ["https://www.nike.com/android-icon-192x192.png"]
    shoe = tmp_path / "shoe.jpg"
    Image.new("RGB", (1000, 1200)).save(shoe)
    asked = []
    monkeypatch.setattr(creatives, "ask_human", lambda *a, **k: asked.append(k.get("upload")) or str(shoe))
    new, update = creatives.ensure_visual("c1", brief)
    assert asked == ["Upload product photos"] and new.products[0].images == [str(shoe)] and update["brief"]

    monkeypatch.setattr(creatives, "ask_human", lambda *a, **k: creatives.NO_PHOTO)
    general, update = creatives.ensure_visual("c1", brief)  # general brand ads: remembered on the brief
    assert general.creative_focus == "brand" and update["brief"]["creative_focus"] == "brand"

    monkeypatch.setattr(creatives, "ask_human", lambda *a, **k: pytest.fail("asked twice"))
    assert creatives.ensure_visual("c1", general) == (general, {})  # never asked again


def test_client_photos_are_the_ad_image_and_catalogs_rotate_products(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "background", lambda *a, **k: pytest.fail("generated an image in photos mode"))
    monkeypatch.setattr(creatives.media, "MEDIA", tmp_path)
    photo = tmp_path / "cafe.jpg"
    Image.new("RGB", (1200, 1200)).save(photo)
    brief = ClientBrief.model_validate(load("brief") | {"creative_focus": "photos", "photos": [str(photo)]})
    jobs = creatives.plan_jobs(creatives.CampaignBlueprint.model_validate(load("blueprint")), {})
    bgs = creatives.Studio("c1", brief).backgrounds([(j, creatives.HookPlus(id="", adset_ref="", text="x")) for j in jobs])
    assert {src for _, src in bgs.values()} == {"client-photo"} and {p for p, _ in bgs.values()} == {str(photo)}

    from malav import strategy
    from core.schemas import ResearchReport
    cat = ClientBrief.model_validate(load("brief") | {"products": [
        {"name": "Air Max", "images": [str(photo)]}, {"name": "Jordan 1", "images": [str(photo)]}]})
    research = ResearchReport.model_validate(load("research"))
    plan = strategy._fallback(research)
    bp = strategy.build_blueprint("c1", cat, research, plan, 2)
    assert [s.product for s in bp.adsets()] == ["Air Max", "Jordan 1"]  # each ad set sells its own product


def test_copy_findings_rewrite_copy_and_keep_images(monkeypatch):
    monkeypatch.setattr(loops, "produce", lambda *a, **k: pytest.fail("regenerated an image for a copy problem"))
    monkeypatch.setattr(loops, "demo_mode", lambda s: False)
    fixed = AdCopy(primary_text="Every pair checked before it ships.", headline="Authentic, always", cta="SHOP_NOW")
    monkeypatch.setattr(loops, "rewrite_copy", lambda cid, brief, copy, issues: fixed)
    pkg = load("package")
    target = pkg["creatives"][0]
    finding = Finding(severity="major", dimension="copy_policy", owner="malav", message="implies anxiety",
                      object_ref=f"{target['creative_id']} – primary_text")
    state = {"client_id": "demo_brewbloom", "brief": load("brief"), "package": pkg, "hooks": load("hooks"),
             "audit": AuditReport(decision="FAIL", scores={}, findings=[finding]).model_dump(mode="json")}
    from auditor.agent import refs
    prompt = FixPrompt(owner="malav", object_refs=refs(finding.object_ref), prompt="fix the copy")
    out = CreativePackage.model_validate(loops.apply_fixes(state, [prompt])["package"])
    same_ad = [c for c in out.creatives if c.ad_ref == target["ad_ref"]]
    assert same_ad and all(c.ad_copy.headline == "Authentic, always" for c in same_ad)  # every ratio of the ad
    assert all(c.path == o["path"] for c, o in zip(out.creatives, pkg["creatives"]))  # images untouched
