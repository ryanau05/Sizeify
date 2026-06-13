"""DEMO router — the headline flow + closet read.

Endpoints (all under ``/demo``):
  POST /demo/recommend-from-url   paste-URL -> recommendation (the headline moment)
  POST /demo/seed                 (re)seed the demo user, closet, brand products
  GET  /demo/closet               the demo user's closet (for the web client)
  POST /demo/closet/garments      add a garment (DEMO-08, still stubbed)

This stands in for the native share-extension hot path (PRD §9.2). It calls the
SAME real domain core as production — only the scraper and LLM edges are stubbed.

THROWAWAY glue (DEMO-07). Single-user demo: the user is resolved by the seeded
email rather than from the JWT, so the flow works even without auth wired up.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.demo import stub_llm, stub_scraper
from api.demo._normalize import normalize_size_chart
from api.demo.seed_demo import DEMO_USER_EMAIL
from api.deps import (
    BrandProductRepositoryDep,
    FitSignalRepositoryDep,
    OwnedGarmentRepositoryDep,
    RecommendationRepositoryDep,
    UserRepositoryDep,
)
from api.domain.fit_profile import (
    ClosetSnapshot,
    GarmentSnapshot,
    SignalSnapshot,
    build_fit_profile,
)
from api.domain.matching import BrandProduct as EngineBrandProduct
from api.domain.recommendation import recommend
from api.models import OwnedGarment, User
from api.repositories.base import transaction
from api.schemas.enums import OverallRating, StretchLevel, Verdict

router = APIRouter(prefix="/demo", tags=["demo"])


class RecommendUrlRequest(BaseModel):
    url: str


def _enum_or_none(enum_cls: Any, value: Any) -> Any:
    return enum_cls(value) if value else None


async def _demo_user(user_repo: UserRepositoryDep) -> User:
    for user in await user_repo.list(limit=500):
        if user.email == DEMO_USER_EMAIL:
            return user
    raise HTTPException(
        status_code=409,
        detail="Demo not seeded. Run `make demo-seed` first.",
    )


async def _closet_garments(
    user_id: Any, og_repo: OwnedGarmentRepositoryDep
) -> list[OwnedGarment]:
    return [g for g in await og_repo.list(limit=500) if g.user_id == user_id]


def _build_snapshot(
    category_id: str,
    garments: list[OwnedGarment],
    signals_by_garment: dict[Any, list[Any]],
) -> ClosetSnapshot:
    snaps: list[GarmentSnapshot] = []
    for g in garments:
        snaps.append(
            GarmentSnapshot(
                label=g.product_name or f"{g.brand} {g.size_label}",
                brand=g.brand,
                size_label=g.size_label,
                measurements_cm={k: float(v) for k, v in g.measurements.items()},
                stretch_level=_enum_or_none(StretchLevel, g.stretch_level),
                overall_rating=_enum_or_none(OverallRating, g.overall_rating),
                use_cases=tuple(g.use_cases or ()),
                signals=tuple(
                    SignalSnapshot(
                        dimension=s.dimension,
                        verdict=Verdict(s.verdict),
                        magnitude_cm=(
                            float(s.magnitude_cm) if s.magnitude_cm is not None else None
                        ),
                        use_case=s.use_case,
                    )
                    for s in signals_by_garment.get(g.id, [])
                ),
            )
        )
    return ClosetSnapshot(category_id=category_id, garments=snaps)


@router.post("/recommend-from-url")
async def recommend_from_url(
    body: RecommendUrlRequest,
    user_repo: UserRepositoryDep,
    og_repo: OwnedGarmentRepositoryDep,
    fs_repo: FitSignalRepositoryDep,
    bp_repo: BrandProductRepositoryDep,
    rec_repo: RecommendationRepositoryDep,
) -> dict[str, Any]:
    """Headline flow: product URL -> size recommendation (all four §5.4 parts)."""
    # 1. Resolve the URL to a brand product via the stubbed scraper.
    try:
        product = stub_scraper.resolve(body.url)
    except stub_scraper.UnknownBrandError as exc:
        raise HTTPException(
            status_code=422,
            detail="That doesn't look like one of our partner brands yet.",
        ) from exc

    # 2. Build the user's fit profile from their seeded closet.
    user = await _demo_user(user_repo)
    garments = await _closet_garments(user.id, og_repo)
    if not garments:
        raise HTTPException(status_code=409, detail="Demo closet is empty; run the seed.")
    garment_ids = {g.id for g in garments}
    signals_by_garment: dict[Any, list[Any]] = {}
    for sig in await fs_repo.list(limit=2000):
        if sig.owned_garment_id in garment_ids:
            signals_by_garment.setdefault(sig.owned_garment_id, []).append(sig)
    category_id = garments[0].category_id
    profile = build_fit_profile(_build_snapshot(category_id, garments, signals_by_garment))

    # 3. Match + recommend (the real engine).
    engine_product = EngineBrandProduct(
        brand=product["brand"],
        product_name=product["product_name"],
        size_chart=normalize_size_chart(product["size_chart"]),
        stretch_level=_enum_or_none(StretchLevel, product.get("stretch_level")),
    )
    rec = recommend(profile, engine_product)

    # 4. Persist the recommendation (prompt_version='demo-v0'). Best-effort:
    #    a persistence hiccup must not swallow the headline result.
    try:
        bp_row = next(
            (b for b in await bp_repo.list(limit=500) if b.product_url == product["url"]),
            None,
        )
        if bp_row is not None:
            by_brand_size = {(g.brand, g.size_label): g.id for g in garments}
            ref_ids = [
                by_brand_size[(r.brand, r.size_label)]
                for r in rec.reference_garments
                if (r.brand, r.size_label) in by_brand_size
            ]
            async with transaction(rec_repo.session):
                await rec_repo.create(
                    user_id=user.id,
                    brand_product_id=bp_row.id,
                    recommended_size=rec.primary.size_label,
                    confidence=Decimal(str(rec.confidence)),
                    fit_notes={
                        "primary": list(rec.primary.fit_notes),
                        "alternate": list(rec.alternate.fit_notes) if rec.alternate else None,
                    },
                    reference_garment_ids=ref_ids,
                    prompt_version="demo-v0",
                )
    except Exception:  # noqa: BLE001 — demo: never fail the response on persist
        pass

    return rec.to_wire()


@router.post("/seed")
async def seed() -> dict[str, Any]:
    """Idempotently (re)seed the demo dataset. Delegates to ``seed_demo.run``."""
    from api.demo import seed_demo

    await seed_demo.run()
    return {"status": "seeded"}


@router.get("/closet")
async def get_closet(
    user_repo: UserRepositoryDep,
    og_repo: OwnedGarmentRepositoryDep,
) -> list[dict[str, Any]]:
    """Return the demo user's closet in the web client's ``Garment`` shape."""
    user = await _demo_user(user_repo)
    garments = await _closet_garments(user.id, og_repo)
    return [
        {
            "id": str(g.id),
            "label": g.product_name or f"{g.brand} {g.size_label}",
            "brand": g.brand,
            "size_label": g.size_label,
            "stretch_level": g.stretch_level or "none",
            "measurements_cm": {k: float(v) for k, v in g.measurements.items()},
        }
        for g in garments
    ]


@router.post("/closet/garments")
async def add_garment() -> dict[str, Any]:
    """Add a garment from the web onboarding flow (DEMO-08).

    Free-text feedback is run through ``stub_llm.extract`` to produce
    ``nlp_extracted`` fit signals. Not on the headline path (the demo shell's
    two tabs are Recommend + Closet), so left for DEMO-08/10.
    """
    _ = stub_llm  # keep the import live for DEMO-08
    raise HTTPException(status_code=501, detail="DEMO-08: add-garment not implemented yet")
