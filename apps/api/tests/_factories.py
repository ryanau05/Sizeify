"""Async test factories for FK predecessors.

Plain coroutines (not fixtures) — repo tests typically need one or two
predecessor rows and grabbing them by function call is less ceremonious
than juggling fixture dependencies. Each factory writes via the raw
session (not via a repository) so repo tests exercise the repo-under-test
on a clean slate, not via two repos in a row.

A row inserted here lives only inside the per-test SAVEPOINT-isolated
session from ``conftest.db_session`` — teardown rolls it back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import password as auth_password
from api.models import (
    BrandProduct,
    FitSignal,
    GarmentCategory,
    OwnedGarment,
    Recommendation,
    User,
)


def _short_id() -> str:
    """8-char hex chunk for unique-but-readable test identifiers."""
    return uuid.uuid4().hex[:8]


# Credentials for fixture users. The hash is computed once at import
# rather than pasted in as a literal: argon2 is deliberately ~75 ms so
# per-call hashing would tax every test that touches a user, but a
# literal would silently stop matching ``TEST_PASSWORD`` the moment
# ``auth.password``'s parameters are recalibrated.
TEST_PASSWORD = "TestPassw0rd!"
TEST_PASSWORD_HASH = auth_password.hash(TEST_PASSWORD)


async def make_user(
    session: AsyncSession,
    *,
    email: str | None = None,
    **overrides: Any,
) -> User:
    fields: dict[str, Any] = {
        "email": email or f"test-{_short_id()}@example.com",
        "password_hash": TEST_PASSWORD_HASH,
        # PRD §11 consent record. NOT NULL, so every fixture user needs
        # one; the value only matters to tests that assert on it.
        "privacy_consent_accepted_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    user = User(**fields)
    session.add(user)
    await session.flush()
    return user


async def make_garment_category(
    session: AsyncSession,
    *,
    id: str | None = None,
    **overrides: Any,
) -> GarmentCategory:
    # Schemas are minimal — repo tests only care that the row exists and
    # the JSONB round-trips. TKT-P1-02's seed owns the canonical
    # button-down schema.
    fields: dict[str, Any] = {
        "id": id or f"test_category_{_short_id()}",
        "display_name": "Test category",
        "measurement_schema": {"dimensions": []},
        "dimension_weights": {},
    }
    fields.update(overrides)
    category = GarmentCategory(**fields)
    session.add(category)
    await session.flush()
    return category


async def make_owned_garment(
    session: AsyncSession,
    *,
    user: User | None = None,
    category: GarmentCategory | None = None,
    **overrides: Any,
) -> OwnedGarment:
    user = user or await make_user(session)
    category = category or await make_garment_category(session)
    fields: dict[str, Any] = {
        "user_id": user.id,
        "category_id": category.id,
        "brand": "Test Brand",
        "size_label": "M",
        # Keyed by canonical dimension name, values as stored by
        # ``routes.closet`` — cm-only (CLAUDE.md domain conventions), with
        # provenance kept for PRD §A.9.
        "measurements": {
            "chest": {"value": 54.0, "unit": "cm", "source": "manual_tape"},
            "body_length": {"value": 71.0, "unit": "cm", "source": "manual_tape"},
        },
    }
    fields.update(overrides)
    garment = OwnedGarment(**fields)
    session.add(garment)
    await session.flush()
    return garment


async def make_fit_signal(
    session: AsyncSession,
    *,
    owned_garment: OwnedGarment | None = None,
    **overrides: Any,
) -> FitSignal:
    owned_garment = owned_garment or await make_owned_garment(session)
    fields: dict[str, Any] = {
        "owned_garment_id": owned_garment.id,
        "dimension": "chest",
        "verdict": "preferred",
        "source": "user_added",
        "raw_feedback_text": "User-added: chest preferred",
    }
    fields.update(overrides)
    signal = FitSignal(**fields)
    session.add(signal)
    await session.flush()
    return signal


async def make_brand_product(
    session: AsyncSession,
    *,
    category: GarmentCategory | None = None,
    product_url: str | None = None,
    **overrides: Any,
) -> BrandProduct:
    category = category or await make_garment_category(session)
    fields: dict[str, Any] = {
        "brand": "Test Brand",
        "product_name": "Test Oxford",
        "product_url": product_url or f"https://example.com/p/{_short_id()}",
        "category_id": category.id,
        "size_chart": {"M": {"chest_cm": 54, "body_length_cm": 71}},
    }
    fields.update(overrides)
    product = BrandProduct(**fields)
    session.add(product)
    await session.flush()
    return product


async def make_recommendation(
    session: AsyncSession,
    *,
    user: User | None = None,
    brand_product: BrandProduct | None = None,
    **overrides: Any,
) -> Recommendation:
    user = user or await make_user(session)
    brand_product = brand_product or await make_brand_product(session)
    fields: dict[str, Any] = {
        "user_id": user.id,
        "brand_product_id": brand_product.id,
        "recommended_size": "M",
        "confidence": Decimal("0.850"),
        "fit_notes": {"chest": "matches your favorite Oxford"},
        "prompt_version": "manual-v0",
    }
    fields.update(overrides)
    recommendation = Recommendation(**fields)
    session.add(recommendation)
    await session.flush()
    return recommendation
