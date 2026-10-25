"""Real-world campaign shapes: local businesses, restricted categories, audiences & retargeting, offers that end,
festival budget boosts, several languages, catalog ads, and the live money alarms."""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from core import compliance, langs
from core.schemas import (AdSetPlan, BudgetPlan, CampaignBlueprint, ClientBrief, CreativePackage, InsightRow,
                          LaunchResult, Location, Offer, ResearchReport, TargetingSpec)
from malav import hooks, media, strategy
from shlok import audiences, budget, launch, live, targeting

FIX = Path(__file__).resolve().parent.parent / "fixtures"
load = lambda n: json.loads((FIX / f"{n}.json").read_text(encoding="utf-8"))
brief_of = lambda **k: ClientBrief.model_validate(load("brief") | k)
adset = lambda **k: AdSetPlan(**({"ref": "as_x", "name": "X", "persona_ref": "p_wfh", "angle": "a", "ads": []} | k))


def test_local_business_targets_around_its_locations():
    b = brief_of(locations=[Location(name="Bandra studio", lat=19.06, lng=72.83, radius_km=3, pincodes=["400050"])])
    spec = targeting.build_spec(b, adset(), None, {"4:5"}).spec
    assert spec["geo_locations"]["custom_locations"][0] | {"radius": 3} == spec["geo_locations"]["custom_locations"][0]
    assert spec["geo_locations"]["zips"] == [{"key": "IN:400050"}] and "countries" not in spec["geo_locations"]
    # housing / credit / jobs: Meta's minimum radius and no postcodes
    special = targeting.build_spec(b.model_copy(update={"special_ad_category": "housing"}), adset(), None, {"4:5"}).spec
    assert special["geo_locations"]["custom_locations"][0]["radius"] >= 25 and "zips" not in special["geo_locations"]


def test_restricted_categories_shape_targeting_copy_and_audit():
    beer = brief_of(industry="craft beer brewery")
    assert [r.category for r in compliance.rules_for(beer)] == ["alcohol"]
    assert targeting.build_spec(beer, adset(), None, {"4:5"}).spec["age_min"] >= 25
    assert "minors" in hooks.situation(beer, adset())
    from auditor.agent import deterministic_checks
    state = {"brief": brief_of(industry="online betting app").model_dump(mode="json"), "package": load("package"),
             "budget_plan": budget.allocate(brief_of(), CampaignBlueprint.model_validate(load("blueprint"))).model_dump(mode="json")}
    found = [f for f in deterministic_checks(state) if f.severity == "blocker" and f.owner == "human"]
    assert found and "authorisation" in found[0].message
    state["brief"] = brief_of(industry="online betting app", authorized=["gambling & real-money games"]).model_dump(mode="json")
    assert not [f for f in deterministic_checks(state) if "authorisation" in f.message]


def test_audiences_retargeting_and_exclusions(monkeypatch, tmp_path):
    monkeypatch.setenv("META_PIXEL_ID", "123")
    monkeypatch.setenv("META_PAGE_ID", "456")
    csv = tmp_path / "customers.csv"
    csv.write_text("name,email,phone\nA, Asha@Example.com ,+91 98200 12345\n", encoding="utf-8")
    b = brief_of(audiences={"customer_list": str(csv)}, budget=load("brief")["budget"] | {"total": 600000})
    assert set(audiences.plan(b)) == {"visitors", "engagers", "purchasers", "customers", "lookalike"}
    rows = audiences.hashed_rows(str(csv))
    assert len(rows["EMAIL_SHA256"][0]) == 64 and "asha@example.com" not in json.dumps(rows)  # never raw
    ids = audiences.ensure(b)
    retarget = targeting.build_spec(b, adset(audience="retargeting"), None, {"4:5"}, audience_ids=ids).spec
    assert {a["id"] for a in retarget["custom_audiences"]} == {"dry_aud_visitors", "dry_aud_engagers"}
    prospect = targeting.build_spec(b, adset(), None, {"4:5"}, audience_ids=ids).spec
    assert {a["id"] for a in prospect["excluded_custom_audiences"]} == {"dry_aud_purchasers", "dry_aud_customers"}
    lal = targeting.build_spec(b, adset(ref="as_x_lal"), None, {"4:5"}, audience_ids=ids).spec
    assert lal["custom_audiences"] == [{"id": "dry_aud_lookalike"}]

    bp = strategy.add_audience_sets("c1", b, CampaignBlueprint.model_validate(load("blueprint")))
    rt = next(s for s in bp.adsets() if s.audience == "retargeting")
    pkg = CreativePackage.model_validate(load("package")).model_copy(update={"blueprint": bp})
    plan = budget.allocate(b, bp)
    specs = [TargetingSpec(adset_ref=s.ref, spec={}, summary="") for s in bp.adsets()]
    built = launch.build(b, pkg, plan, specs)
    reused = [o.local_ref for o in built.objects if o.local_ref.endswith(f"@{rt.ref}")]
    assert len(reused) == len([c for c in pkg.creatives if c.adset_ref == rt.reuse_from])  # same creatives, new ads
    creative_posts = [p["local_ref"] for p in built.payloads if p["local_ref"].startswith("creative:")]
    assert len(creative_posts) == len(set(creative_posts))  # a reused creative is made once
    assert all(p["spend_cap"] > 0 for p in built.payloads if p["local_ref"].startswith("campaign:"))


def test_offer_renders_and_its_ads_pause_when_it_ends():
    o = Offer(headline="30% off", code="SAVE30", was_price=999, now_price=699, ends=date.today() + timedelta(days=5))
    html = media.offer_html(o, "₹")
    assert all(x in html for x in ("30% off", "<s>₹999</s>", "₹699", "SAVE30", "Ends"))
    assert "कोड" in media.offer_html(o, "₹", "hi")
    assert media.offer_html(o.model_copy(update={"ends": date.today() - timedelta(days=1)})) == ""

    b = brief_of(offer=o.model_copy(update={"ends": date(2026, 6, 10)}))
    pkg = CreativePackage.model_validate(load("package"))
    pkg.creatives[0].template_id = "offer-badge"
    assert live.offer_ended(b, pkg, date(2026, 6, 10)) == []
    assert live.offer_ended(b, pkg, date(2026, 6, 11)) == [pkg.creatives[0].creative_id]


def test_festival_boosts_come_from_the_reserve():
    b = brief_of(budget=load("brief")["budget"] | {"start": "2026-10-20", "end": "2026-11-30"})
    plan = budget.allocate(b, CampaignBlueprint.model_validate(load("blueprint")))
    names = " ".join(p["name"] for p in plan.peaks)
    assert "Diwali" in names and "Black Friday" in names
    extra = sum(plan.peaks[0]["extra"].values())
    assert extra <= plan.testing_reserve  # paid from the reserve: the total never moves
    assert (sum(plan.adset_daily.values()) + plan.testing_reserve) * plan.days <= b.budget.total


def test_several_languages_split_across_ad_sets():
    b = brief_of(languages=["hi", "en"])
    research = ResearchReport.model_validate(load("research"))
    bp = strategy.build_blueprint("c1", b, research, strategy._fallback(research), 2)
    assert [s.language for s in bp.adsets()] == ["hi", "en"]
    assert "Hindi" in hooks.situation(b, bp.adsets()[0]) and "Hindi" not in hooks.situation(b, bp.adsets()[1])
    assert langs.voice("hi") == "hi-IN-SwaraNeural" and langs.voice("en", "GB") == "en-GB-SoniaNeural"
    assert [len(x) for x in media.captions("日本語のテキストはスペースなしで書かれていますのでこのように", lang="ja")] == [14, 14, 2]


def test_catalog_campaign_for_many_products(monkeypatch):
    products = [{"name": f"Item {i}", "price": 100 + i, "url": f"https://shop.example/p/{i}",
                 "images": [f"https://cdn.example/{i}.jpg"]} for i in range(5)]
    b = brief_of(products=products, catalog_ads=True, budget=load("brief")["budget"] | {"total": 600000})
    bp = strategy.add_audience_sets("c1", b, CampaignBlueprint.model_validate(load("blueprint")))
    assert any(c.ref == "c_catalog" for c in bp.campaigns)
    pkg = CreativePackage.model_validate(load("package")).model_copy(update={"blueprint": bp})
    built = launch.build(b, pkg, budget.allocate(b, bp), [TargetingSpec(adset_ref=s.ref, spec={}, summary="")
                                                         for s in bp.adsets()])
    posts = {p["local_ref"]: p for p in built.payloads}
    assert len(posts["catalog:items"]["requests"]) == 5
    assert posts["adset:as_catalog"]["promoted_object"]["product_set_id"] == "dry_product_set:all"
    assert "{{product.name}}" in json.dumps(posts["creative:catalog"])


def test_money_alarms(monkeypatch):
    plan = BudgetPlan(client_id="c1", currency="INR", total=10000, days=10, mode="ABO",
                      bid_strategy="LOWEST_COST_WITHOUT_CAP", testing_reserve=100, campaign_daily={},
                      adset_daily={"a": 900})
    day = lambda d, spend, conv: InsightRow(ts=datetime(2026, 1, d, tzinfo=timezone.utc), ad_ref="cr", adset_ref="a",
                                            spend=spend, conversions=conv)
    kinds = lambda rows: {a.kind for a in live.anomalies(plan, rows)}
    assert kinds([day(1, 1000, 10), day(2, 1000, 10), day(3, 1000, 10), day(4, 1000, 1)]) == {"cpa_spike"}
    assert "overspend" in kinds([day(1, 1000, 10), day(2, 2000, 10)])
    assert "budget_cap" in kinds([day(d, 1000, 5) for d in range(1, 11)])


def test_extras_dropped_when_the_budget_cannot_carry_them(monkeypatch):
    monkeypatch.setenv("META_PIXEL_ID", "123")
    bp = strategy.add_audience_sets("c1", brief_of(), CampaignBlueprint.model_validate(load("blueprint")))
    assert len(bp.adsets()) == 2  # 150k over 30 days only carries the 2 prospecting ad sets


def test_every_language_gets_an_ad_set_even_with_one_idea():
    b = brief_of(languages=["hi", "en"], deliverables=load("brief")["deliverables"] | {"n_images": 2, "n_videos": 0})
    research = ResearchReport.model_validate(load("research"))
    one = strategy._fallback(research).model_copy(update={"ideas": strategy._fallback(research).ideas[:1]})
    bp = strategy.build_blueprint("c1", b, research, one, 2)
    assert [s.language for s in bp.adsets()] == ["hi", "en"] and all(s.ads for s in bp.adsets())


def test_offer_headline_without_its_code():
    assert Offer(headline="20% off with code DIWALI20", code="DIWALI20").short() == "20% off"
    assert Offer(headline="Code EID30: 30% off", code="EID30").short() == "30% off"
    assert Offer(headline="Buy 1 Get 1").short() == "Buy 1 Get 1"
