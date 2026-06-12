"""DEMO seed — one demo user, a 5-garment closet, and brand_product rows.

Idempotent: safe to re-run between demo rehearsals. Uses the REAL repositories
and models so the seeded data exercises the production schema (no demo-only
tables). Run:

    DEMO_MODE=1 uv run python -m api.demo.seed_demo

The 5-garment closet mirrors the Phase 1 exit-criterion fixture (two
preferred-fit, one slightly-tight chest, one slightly-loose body, one
use_case='gym') so the seeded profile is rich enough to produce a confident
recommendation on stage.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

_FIXTURES = Path(__file__).parent / "fixtures"

DEMO_USER_EMAIL = "demo@sizeify.app"
DEMO_USER_PASSWORD = "demo-capstone-2026"  # DEMO ONLY — never a real credential


async def run() -> None:
    """Seed (or refresh) the demo dataset.

    DEMO TODO Day 1:
      1. Upsert the demo user (Argon2 hash via api.auth.password).
      2. Ensure the men's button-down category is seeded
         (reuse api.seeds.garment_categories).
      3. Insert brand_product rows from fixtures/brand_products.json.
      4. Insert the 5 owned_garments + their fit_signals from
         fixtures/demo_closet.json (measurements in cm).
      5. Print the demo user's JWT so the web client can be pre-authed.
    All writes go through api.repositories.* — no raw SQL.
    """
    _closet = json.loads((_FIXTURES / "demo_closet.json").read_text())
    _products = json.loads((_FIXTURES / "brand_products.json").read_text())
    raise NotImplementedError(
        "DEMO TODO Day 1: implement seeding via real repositories"
    )


if __name__ == "__main__":
    asyncio.run(run())
