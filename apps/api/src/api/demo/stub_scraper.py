"""DEMO stub for the Phase 2 scraper service.

Real scrapers (PRD §9.3) fetch live size charts per brand. For the demo we map
a pasted URL to a pre-seeded BrandProduct by hostname, reading fixtures rather
than hitting the network. This keeps the demo deterministic and offline-safe.

Conforms loosely to the eventual scrapers/base.py interface so the swap to real
scrapers later is a drop-in: same return shape, same "unknown brand" behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_FIXTURES = Path(__file__).parent / "fixtures" / "brand_products.json"

# Hostname -> brand slug. Only the 10 v1 brands (PRD §7.6) are recognised.
_HOST_TO_BRAND = {
    "uniqlo.com": "uniqlo",
    "jcrew.com": "jcrew",
    "bonobos.com": "bonobos",
    "everlane.com": "everlane",
    "bananarepublic.com": "banana_republic",
    "brooksbrothers.com": "brooks_brothers",
    "charlestyrwhitt.com": "charles_tyrwhitt",
    "mrporter.com": "mr_porter",
    "spierandmackay.com": "spier_mackay",
    "propercloth.com": "proper_cloth",
}


class UnknownBrandError(Exception):
    """Raised when a hostname isn't one of the 10 v1 partner brands."""


def brand_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    for known, slug in _HOST_TO_BRAND.items():
        if host == known or host.endswith("." + known):
            return slug
    raise UnknownBrandError(host)


def resolve(url: str) -> dict[str, Any]:
    """Return a BrandProduct-shaped dict (with size_chart) for a demo URL.

    DEMO TODO Day 1: ship fixtures/brand_products.json with >=3 products per
    brand and their size charts in cm. Return shape must match what the real
    matching engine expects from a brand_product row.
    """
    slug = brand_for_url(url)
    products: list[dict[str, Any]] = json.loads(_FIXTURES.read_text())
    matches = [p for p in products if p["brand"] == slug]
    if not matches:
        raise UnknownBrandError(slug)
    # Demo: first product for the brand. Real: match by product path/SKU.
    return matches[0]
