"""CRUD tests for ``OwnedGarmentRepository``."""

from __future__ import annotations

from uuid import uuid4

from _factories import (
    make_garment_category,
    make_owned_garment,
    make_user,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api.repositories.owned_garments import OwnedGarmentRepository


async def test_create_and_get(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    category = await make_garment_category(db_session)
    repo = OwnedGarmentRepository(db_session)

    measurements = {
        "chest": {"value": 54.0, "unit": "cm", "source": "manual_tape"},
        "body_length": {"value": 71.0, "unit": "cm", "source": "manual_tape"},
    }
    garment = await repo.create(
        user_id=user.id,
        category_id=category.id,
        brand="Uniqlo",
        product_name="Oxford",
        size_label="M",
        measurements=measurements,
        stretch_level="slight",
        overall_rating="love",
        use_cases=["casual", "work"],
    )

    fetched = await repo.get(garment.id)
    assert fetched is not None
    assert fetched.brand == "Uniqlo"
    assert fetched.product_name == "Oxford"
    # JSONB and ARRAY round-trip intact.
    assert fetched.measurements == measurements
    assert fetched.use_cases == ["casual", "work"]


async def test_get_missing_returns_none(db_session: AsyncSession) -> None:
    repo = OwnedGarmentRepository(db_session)
    assert await repo.get(uuid4()) is None


async def test_list_returns_inserted_rows(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    category = await make_garment_category(db_session)
    await make_owned_garment(db_session, user=user, category=category)
    await make_owned_garment(db_session, user=user, category=category)

    repo = OwnedGarmentRepository(db_session)
    rows = await repo.list()
    assert len([g for g in rows if g.user_id == user.id]) == 2


async def test_update_patches_fields(db_session: AsyncSession) -> None:
    garment = await make_owned_garment(db_session)
    repo = OwnedGarmentRepository(db_session)

    updated = await repo.update(
        garment.id,
        overall_rating="like",
        size_label="L",
    )
    assert updated is not None
    assert updated.overall_rating == "like"
    assert updated.size_label == "L"


async def test_delete_removes_row(db_session: AsyncSession) -> None:
    garment = await make_owned_garment(db_session)
    repo = OwnedGarmentRepository(db_session)

    assert await repo.delete(garment.id) is True
    assert await repo.get(garment.id) is None


async def test_delete_missing_returns_false(db_session: AsyncSession) -> None:
    repo = OwnedGarmentRepository(db_session)
    assert await repo.delete(uuid4()) is False


async def test_list_for_user_honours_a_limit(db_session: AsyncSession) -> None:
    """The default is 100, not unbounded: a caller that forgets ``limit=None``
    gets a silently short closet rather than an error.

    Every v1 caller passes ``limit=None`` deliberately (``GET /closet``, the
    GDPR export, the fit-profile builder), so nothing in production exercises
    the bounded path today — which is exactly why the contract is pinned here
    instead of discovered by the first paging caller.
    """
    user = await make_user(db_session)
    category = await make_garment_category(db_session)
    for _ in range(4):
        await make_owned_garment(db_session, user=user, category=category)

    repo = OwnedGarmentRepository(db_session)

    assert len(await repo.list_for_user(user.id, limit=2)) == 2
    assert len(await repo.list_for_user(user.id, limit=None)) == 4
    assert len(await repo.list_for_user(user.id, limit=2, offset=3)) == 1
