"""Range campaigns: several real products per ad, benefit tags, carousels (a card per product) launched as one ad."""
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from core.schemas import BudgetPlan, ClientBrief, CreativePackage, TargetingSpec
from malav import creatives, hooks, intake, lineup, media, strategy

FIX = Path(__file__).resolve().parent.parent / "fixtures"
load = lambda n: json.loads((FIX / f"{n}.json").read_text(encoding="utf-8"))
CID = "test_range"


@pytest.fixture
def range_brief():
    d = media.MEDIA / CID / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    products = []
    for name, colour, price, usp in [("Serum", "royalblue", 599, ["10% niacinamide", "Fragrance free", "Size: 30 ml"]),
                                     ("Cleanser", "gold", 349, ["pH 5.5"]), ("Sunscreen", "crimson", 499, ["SPF 50"])]:
        im = Image.new("RGBA", (600, 900), (0, 0, 0, 0))
        ImageDraw.Draw(im).rounded_rectangle((120, 100, 480, 860), 40, fill=colour)
        im.save(d / f"{name.lower()}.png")
        products.append({"name": name, "price": price, "usp": usp, "images": [f"media/{CID}/uploads/{name.lower()}.png"]})
    brief = load("brief") | {"client_id": CID, "creative_focus": "range", "products": products}
    brief["deliverables"] |= {"n_images": 3, "n_videos": 0, "ratios": ["4:5", "9:16"]}
    yield brief
    shutil.rmtree(media.MEDIA / CID, ignore_errors=True)


def test_range_intake_answers():
    d = intake.Draft(business_name="Brand", products=[intake.Product(name="A", images=["a.jpg"])])
    assert intake.visual_patch(d, intake.RANGE_OPT) == {"creative_focus": "range"}
    assert intake.visual_patch(d, "general campaign showing our whole range")["creative_focus"] == "range"
    d = d.model_copy(update={"creative_focus": "range"})
    assert not intake.visual_ready(d)  # a range of one product isn't a range: ask for more photos
    assert "at least 2" in intake.visual_question(d)[0]


def test_range_campaign_end_to_end(range_brief, monkeypatch):
    monkeypatch.setenv("RUNE_AUTO_APPROVE", "1")  # the look question picks the first direction
    monkeypatch.setattr(media, "background", lambda prompt, out, w, h, colors, **k: media.gradient(out, w, h, colors) or "gradient")
    monkeypatch.setattr(media, "product_cutout", lambda src, out: media.resolve(src))  # rembg is slow and optional
    state = {"client_id": CID, "intake": {"website": "https://brand.example", "notes": "range"}, "brief": range_brief,
             "research": load("research") | {"client_id": CID}, "context_doc": ""}
    brief = ClientBrief.model_validate(range_brief)
    assert lineup.tags(brief, lineup.shown(brief)) == ["10% niacinamide", "From ₹349", "3 to choose from"]
    assert lineup.card_tags(brief, lineup.shown(brief)[0]) == ["10% niacinamide", "Fragrance free"]  # size last
    dollars = [p.model_copy(update={"currency": "USD"}) for p in lineup.shown(brief)]
    assert not any(t.startswith("From") for t in lineup.tags(brief, dollars))  # a $ price never becomes a ₹ tag

    state |= strategy.strategy_step(state)
    ads = [a for s in state["blueprint"]["campaigns"][0]["adsets"] for a in s["ads"]]
    assert sum(a["format"] == "carousel" for a in ads) == len([s for s in state["blueprint"]["campaigns"][0]["adsets"] if s["ads"]])
    assert all(s["product"] == "" for s in state["blueprint"]["campaigns"][0]["adsets"])  # brand-level ad sets

    state |= hooks.hooks_step(state)
    state |= creatives.creatives_step(state)
    pkg = CreativePackage.model_validate(state["package"])
    car = [c for c in pkg.creatives if c.kind == "carousel"]
    assert car and all(len(c.cards) == 4 for c in car)  # cover + one card per product
    assert [x.headline for x in car[0].cards[1:]] == ["Serum", "Cleanser", "Sunscreen"]
    assert all(Path(media.resolve(x.path)).exists() for c in car for x in c.cards)
    images = [c for c in pkg.creatives if c.kind == "image"]
    if images and images[0].model_used.endswith("+html"):  # Playwright available: the line-up template rendered
        from malav.director import LAYOUTS
        assert {c.template_id for c in images} <= set(LAYOUTS["range"]) and car[0].template_id == "range-lineup"

    from auditor.agent import deterministic_checks
    from shlok import launch
    plan = BudgetPlan(client_id=CID, currency="INR", total=150000, days=30, mode="ABO", bid_strategy="LOWEST_COST_WITHOUT_CAP",
                      testing_reserve=0, campaign_daily={}, adset_daily={s.ref: 1000 for s in pkg.blueprint.adsets()})
    targeting = [TargetingSpec(adset_ref=s.ref, spec={}, summary="") for s in pkg.blueprint.adsets()]
    result = launch.build(brief, pkg, plan, targeting)
    story = next(p for p in result.payloads if p["local_ref"] == f"creative:{car[0].creative_id}")["object_story_spec"]
    kids = story["link_data"]["child_attachments"]
    assert len(kids) == 4 and kids[1]["name"] == "Serum" and all(k["image_hash"] for k in kids)
    state |= {"budget_plan": plan.model_dump(mode="json"), "targeting": [t.model_dump() for t in targeting],
              "launch": result.model_dump(mode="json")}
    blockers = [f for f in deterministic_checks(state) if f.severity == "blocker"]
    assert not blockers, blockers
