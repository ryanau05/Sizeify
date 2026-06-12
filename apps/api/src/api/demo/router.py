"""DEMO router — the two flows the capstone demo must land.

Endpoints (all under ``/demo``):
  POST /demo/recommend-from-url   paste-URL -> recommendation (the headline moment)
  POST /demo/seed                 (re)seed the demo user, closet, brand products
  GET  /demo/closet               the demo user's closet (for the web client)
  POST /demo/closet/garments      add a garment via the web onboarding flow

This stands in for the native share-extension hot path (PRD §9.2). It calls the
SAME real domain core as production — only the scraper and LLM edges are stubbed.

TODO (Day 2): wire each handler to the real repositories + domain functions.
Signatures below are the contract; bodies are stubs to be filled in.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.demo import stub_llm, stub_scraper

router = APIRouter(prefix="/demo", tags=["demo"])


# --- Schemas (demo-local; production schemas live in api.schemas) -----------
# Reuse api.schemas wire-shapes where they already exist (FitProfileResponse,
# closet schemas). Define only demo-specific request bodies here.


@router.post("/recommend-from-url")
async def recommend_from_url() -> dict[str, Any]:
    """Headline flow: product URL -> size recommendation.

    Day-2 implementation outline:
      1. stub_scraper.resolve(url) -> BrandProduct (fixture lookup by hostname).
      2. Load the demo user's closet snapshot via owned_garments repo.
      3. fit_profile = api.domain.fit_profile.build_fit_profile(snapshot).
      4. rec = api.domain.recommendation.recommend(fit_profile, brand_product).
      5. Persist via recommendations repo (prompt_version='demo-v0').
      6. Return all four PRD §5.4 components: size, confidence, fit_notes,
         reference_garments. Confidence < 60% => two candidates.
    """
    raise NotImplementedError("DEMO TODO Day 2: wire scraper -> profile -> match")


@router.post("/seed")
async def seed() -> dict[str, Any]:
    """Idempotently (re)seed the demo dataset. Delegates to seed_demo."""
    raise NotImplementedError("DEMO TODO Day 1: call api.demo.seed_demo.run()")


@router.get("/closet")
async def get_closet() -> dict[str, Any]:
    """Return the demo user's closet for the web client."""
    raise NotImplementedError("DEMO TODO Day 2: read via owned_garments repo")


@router.post("/closet/garments")
async def add_garment() -> dict[str, Any]:
    """Add a garment from the web onboarding flow.

    Free-text fit feedback (if present) is run through stub_llm.extract() to
    produce fit_signal rows with source='nlp_extracted', mirroring the real
    Phase 3 flow so the demo shows the magic without live LLM calls.
    """
    _ = (stub_scraper, stub_llm)  # referenced so the imports are live for Day 2
    raise NotImplementedError("DEMO TODO Day 2: persist garment + stub extraction")
