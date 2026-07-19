"""DEMO seed — one demo user, a 5-garment closet, and brand_product rows.

Idempotent: safe to re-run between demo rehearsals. Uses the REAL repositories
and models so the seeded data exercises the production schema (no demo-only
tables). Run:

    DEMO_MODE=1 uv run python -m api.demo.seed_demo

The 5-garment closet mirrors the Phase 1 exit-criterion fixture (preferred-fit
shirts, one slightly-tight chest, one slightly-loose body, one use_case='gym')
so the seeded profile is rich enough to produce a confident recommendation.

THROWAWAY glue (DEMO-05). The dimension-name normalization confines the
fixtures' shorthand to the demo layer; the real engine stays canonical.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.auth import jwt as jwt_mod
from api.config import get_settings
from api.demo._normalize import canonical_dim, normalize_measurements, normalize_size_chart
from api.models import BrandProduct, FitSignal, OwnedGarment, User
from api.repositories.base import get_sessionmaker, transaction
from api.repositories.refresh_tokens import RefreshTokenRepository
from api.seeds.garment_categories import seed_garment_categories

_FIXTURES = Path(__file__).parent / "fixtures"

DEMO_USER_EMAIL = "demo@sizeify.app"
# The web client authenticates with a pre-issued JWT (printed below), so no
# password is stored — the v1 `user` table has no password column yet anyway.


async def run() -> None:
    """Seed (or refresh) the demo dataset and print a pre-auth JWT."""
    closet = json.loads((_FIXTURES / "demo_closet.json").read_text())
    products = json.loads((_FIXTURES / "brand_products.json").read_text())
    category_slug = closet["category_slug"]

    session_maker = get_sessionmaker()
    async with session_maker() as session, transaction(session):
        # 1. Category (idempotent upsert; reuses the real seed).
        await seed_garment_categories(session)

        # 2. Demo user (by unique email).
        user = (
            await session.execute(sa.select(User).where(User.email == DEMO_USER_EMAIL))
        ).scalar_one_or_none()
        if user is None:
            user = User(email=DEMO_USER_EMAIL, stated_fit_preference="regular")
            session.add(user)
            await session.flush()

        # 3. Idempotency: wipe the existing demo closet (signals cascade).
        existing = (
            (await session.execute(sa.select(OwnedGarment).where(OwnedGarment.user_id == user.id)))
            .scalars()
            .all()
        )
        for garment in existing:
            await session.delete(garment)
        await session.flush()

        # 4. Owned garments + their fit signals (measurements in cm).
        for item in closet["garments"]:
            garment = OwnedGarment(
                user_id=user.id,
                category_id=category_slug,
                brand=item["brand"],
                product_name=item.get("label"),
                size_label=item["size_label"],
                measurements=normalize_measurements(item["measurements_cm"]),
                stretch_level=item.get("stretch_level"),
                overall_rating=item.get("overall_rating"),
                use_cases=[item["use_case"]] if item.get("use_case") else [],
            )
            session.add(garment)
            await session.flush()
            for sig in item.get("signals", []):
                dimension = canonical_dim(sig["dimension"])
                session.add(
                    FitSignal(
                        owned_garment_id=garment.id,
                        dimension=dimension,
                        verdict=sig["verdict"],
                        magnitude_cm=sig.get("magnitude_cm"),
                        use_case=sig.get("use_case"),
                        source=sig.get("source", "user_added"),
                        raw_feedback_text=(
                            sig.get("raw_feedback_text")
                            or f"User-added: {dimension} {sig['verdict']}"
                        ),
                    )
                )

        # 5. Brand products (idempotent upsert on the unique product_url).
        for product in products:
            chart = normalize_size_chart(product["size_chart"])
            stmt = (
                pg_insert(BrandProduct)
                .values(
                    brand=product["brand"],
                    product_name=product["product_name"],
                    product_url=product["url"],
                    category_id=category_slug,
                    stretch_level=product.get("stretch_level"),
                    size_chart=chart,
                    scraper_version=product.get("scraper_version", "demo-v0"),
                )
                .on_conflict_do_update(
                    index_elements=[BrandProduct.product_url],
                    set_={
                        "product_name": product["product_name"],
                        "size_chart": chart,
                        "scraper_version": product.get("scraper_version", "demo-v0"),
                    },
                )
            )
            await session.execute(stmt)

        # 6. Pre-auth JWT for the web client (skips a login screen).
        token: str | None = None
        if get_settings().jwt_secret:
            token, _refresh = await jwt_mod.issue_pair(user.id, RefreshTokenRepository(session))

    print("✅ Demo data seeded.")
    print(f"   user            : {DEMO_USER_EMAIL} (id {user.id})")
    print(f"   closet garments : {len(closet['garments'])}")
    print(f"   brand products  : {len(products)}")
    if token:
        print("\n   Paste this into apps/web/.env to skip the login screen:")
        print(f"   VITE_DEMO_JWT={token}")
    else:
        print("\n   JWT_SECRET is empty — set it in apps/api/.env to get a pre-auth token.")


if __name__ == "__main__":
    asyncio.run(run())
