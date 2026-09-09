"""CRUD tests for ``GarmentCategoryRepository``.

Exercises the TEXT-PK variant of the generic ``Repository`` contract
(other entities use UUID).
"""

from __future__ import annotations

from _factories import make_garment_category
from sqlalchemy.ext.asyncio import AsyncSession

from api.repositories.garment_categories import GarmentCategoryRepository


async def test_create_and_get(db_session: AsyncSession) -> None:
    repo = GarmentCategoryRepository(db_session)

    schema = {
        "dimensions": [
            {"name": "chest", "unit": "cm", "min_cm": 35.0, "max_cm": 80.0},
        ]
    }
    weights = {"chest": 1.0}
    category = await repo.create(
        id="test_button_down",
        display_name="Test button-down",
        measurement_schema=schema,
        dimension_weights=weights,
    )

    fetched = await repo.get("test_button_down")
    assert fetched is not None
    assert fetched.id == category.id
    assert fetched.display_name == "Test button-down"
    # JSONB round-trips intact.
    assert fetched.measurement_schema == schema
    assert fetched.dimension_weights == weights


async def test_get_missing_returns_none(db_session: AsyncSession) -> None:
    repo = GarmentCategoryRepository(db_session)
    assert await repo.get("does_not_exist") is None


async def test_list_returns_inserted_rows(db_session: AsyncSession) -> None:
    repo = GarmentCategoryRepository(db_session)
    await make_garment_category(db_session, id="cat_a")
    await make_garment_category(db_session, id="cat_b")

    ids = {c.id for c in await repo.list()}
    assert {"cat_a", "cat_b"} <= ids


async def test_update_patches_jsonb(db_session: AsyncSession) -> None:
    repo = GarmentCategoryRepository(db_session)
    category = await make_garment_category(db_session)

    new_weights = {"chest": 0.5, "sleeve_length": 0.5}
    updated = await repo.update(category.id, dimension_weights=new_weights)
    assert updated is not None
    assert updated.dimension_weights == new_weights


async def test_delete_removes_row(db_session: AsyncSession) -> None:
    repo = GarmentCategoryRepository(db_session)
    category = await make_garment_category(db_session)

    assert await repo.delete(category.id) is True
    assert await repo.get(category.id) is None


async def test_delete_missing_returns_false(db_session: AsyncSession) -> None:
    repo = GarmentCategoryRepository(db_session)
    assert await repo.delete("does_not_exist") is False
