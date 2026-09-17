"""CRUD tests for ``RecommendationRepository``.

Covers the ``prompt_version`` invariant (CLAUDE.md: required on every row),
the reference_garment_ids UUID array round-trip, and the default-pending
outcome from the schema.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from _factories import (
    make_brand_product,
    make_owned_garment,
    make_recommendation,
    make_user,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api.repositories.recommendations import RecommendationRepository


async def test_create_and_get(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    brand_product = await make_brand_product(db_session)
    # Use the owned garment's id as a reference — exercises the UUID[]
    # round-trip and matches what TKT-P1-15 will do.
    reference_garment = await make_owned_garment(db_session, user=user)
    repo = RecommendationRepository(db_session)

    fit_notes = {
        "chest": {
            "delta_cm": -1.0,
            "narrative": "1cm tighter than your favorite Uniqlo Oxford",
        }
    }
    recommendation = await repo.create(
        user_id=user.id,
        brand_product_id=brand_product.id,
        recommended_size="M",
        confidence=Decimal("0.870"),
        fit_notes=fit_notes,
        reference_garment_ids=[reference_garment.id],
        use_case_assumed="casual",
        prompt_version="manual-v0",
    )

    fetched = await repo.get(recommendation.id)
    assert fetched is not None
    assert fetched.recommended_size == "M"
    assert fetched.confidence == Decimal("0.870")
    assert fetched.fit_notes == fit_notes
    assert fetched.reference_garment_ids == [reference_garment.id]
    assert fetched.prompt_version == "manual-v0"
    # Schema-level default.
    assert fetched.outcome == "pending"


async def test_get_missing_returns_none(db_session: AsyncSession) -> None:
    repo = RecommendationRepository(db_session)
    assert await repo.get(uuid4()) is None


async def test_list_returns_inserted_rows(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    await make_recommendation(db_session, user=user)
    await make_recommendation(db_session, user=user)

    repo = RecommendationRepository(db_session)
    rows = await repo.list()
    assert len([r for r in rows if r.user_id == user.id]) == 2


async def test_update_records_outcome(db_session: AsyncSession) -> None:
    recommendation = await make_recommendation(db_session)
    repo = RecommendationRepository(db_session)

    from datetime import UTC, datetime

    confirmed_at = datetime.now(UTC)
    updated = await repo.update(
        recommendation.id, outcome="correct", outcome_confirmed_at=confirmed_at
    )
    assert updated is not None
    assert updated.outcome == "correct"
    assert updated.outcome_confirmed_at == confirmed_at


async def test_delete_removes_row(db_session: AsyncSession) -> None:
    recommendation = await make_recommendation(db_session)
    repo = RecommendationRepository(db_session)

    assert await repo.delete(recommendation.id) is True
    assert await repo.get(recommendation.id) is None


async def test_delete_missing_returns_false(db_session: AsyncSession) -> None:
    repo = RecommendationRepository(db_session)
    assert await repo.delete(uuid4()) is False


async def test_list_for_user_is_scoped_and_ordered(db_session: AsyncSession) -> None:
    """Written out of order with explicit timestamps: Postgres ``now()`` is
    transaction-start time, so rows created inside one test transaction all
    share a ``created_at`` and could not show real ordering.
    """
    alice = await make_user(db_session)
    bob = await make_user(db_session)
    product = await make_brand_product(db_session)
    for size, day in (("L", 3), ("S", 1), ("M", 2)):
        await make_recommendation(
            db_session,
            user=alice,
            brand_product=product,
            recommended_size=size,
            created_at=datetime(2026, 1, day, tzinfo=UTC),
        )
    await make_recommendation(db_session, user=bob, brand_product=product)

    rows = await RecommendationRepository(db_session).list_for_user(alice.id)

    assert [row.recommended_size for row in rows] == ["S", "M", "L"]
    assert all(row.user_id == alice.id for row in rows)


async def test_list_for_user_honours_a_limit(db_session: AsyncSession) -> None:
    """Unbounded by default for the GDPR export; the Phase 6 recent-activity
    view is the caller that will page."""
    user = await make_user(db_session)
    product = await make_brand_product(db_session)
    for _ in range(4):
        await make_recommendation(db_session, user=user, brand_product=product)

    rows = await RecommendationRepository(db_session).list_for_user(user.id, limit=2)

    assert len(rows) == 2
