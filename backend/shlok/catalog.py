"""Advantage+ catalog ads: every product in a Meta catalog; Meta shows each person the products they're likeliest to buy.

eligible(brief) -> the products that can go in a catalog (public photo URL, a price in the campaign currency).
launch.build calls prepare() before the "catalog" ad set (creates or reuses META_CATALOG_ID, uploads the items with
items_batch so no public feed URL is needed, makes a product set) and ad() after it (a dynamic creative whose text
Meta fills per product: {{product.name}}, {{product.price}}).
"""
from __future__ import annotations

import os
import re

from core.schemas import ClientBrief, Product

from . import meta

MIN_PRODUCTS = 4


def eligible(brief: ClientBrief) -> list[Product]:
    return [p for p in brief.products
            if p.images and p.images[0].startswith("http") and p.price_in(brief.budget.currency) and p.url]


def can_run(brief: ClientBrief) -> bool:
    return brief.catalog_ads and len(eligible(brief)) >= MIN_PRODUCTS


def item(brief: ClientBrief, p: Product) -> dict:
    """One product in Meta's catalog item format."""
    pid = re.sub(r"[^a-z0-9]+", "-", p.name.lower()).strip("-")[:100]
    return {"id": pid, "title": p.name[:150], "description": ("; ".join(p.usp) or p.name)[:5000],
            "availability": "in stock", "condition": "new", "brand": brief.business_name[:100],
            "price": f"{p.price_in(brief.budget.currency):.2f} {brief.budget.currency}", "link": p.url,
            "image_link": p.images[0]}


def prepare(brief: ClientBrief, make) -> str:
    """Catalog + items + a product set of them, through launch's `make` (idempotent, dry-run aware). Returns the
    product set id the catalog ad set promotes."""
    products = eligible(brief)
    catalog = os.getenv("META_CATALOG_ID") or make(
        "catalog:main", "catalog", f"{os.getenv('META_BUSINESS_ID', 'BUSINESS_ID')}/owned_product_catalogs",
        {"name": f"{brief.business_name} · Rune"})
    batch = {"item_type": "PRODUCT_ITEM", "allow_upsert": True,
             "requests": [{"method": "UPDATE", "data": item(brief, p)} for p in products]}
    # items_batch answers with batch handles, not an object id
    make("catalog:items", "catalog", f"{catalog}/items_batch", batch,
         upload=lambda: (meta.api("POST", f"{catalog}/items_batch", data=batch).get("handles") or ["batch"])[0])
    return make("product_set:all", "product_set", f"{catalog}/product_sets", {
        "name": f"All products ({len(products)})",
        "filter": {"retailer_id": {"is_any": [item(brief, p)["id"] for p in products]}}})


def ad(brief: ClientBrief, make, acct: str, adset_id: str, adset_name: str, pset: str, page: str, cta: str,
       landing: str) -> None:
    """The dynamic ad: Meta fills name and price per product."""
    creative = make("creative:catalog", "creative", f"{acct}/adcreatives", {
        "name": f"{brief.business_name} catalog", "product_set_id": pset,
        "object_story_spec": {"page_id": page, "template_data": {
            "message": f"{brief.business_name}: find yours.", "link": landing, "name": "{{product.name}}",
            "description": "{{product.price}}", "call_to_action": {"type": cta}, "multi_share_end_card": False}}})
    make("ad:catalog", "ad", f"{acct}/ads", {"name": f"{adset_name} · catalog", "adset_id": adset_id,
                                            "creative": {"creative_id": creative}, "status": "PAUSED"})
