"""CRUD tests for ``BrandProductRepository``."""

from __future__ import annotations

from uuid import uuid4

from _factories import make_brand_product, make_garment_category
from sqlalchemy.ext.asyncio import AsyncSession

from api.repositories.brand_products import BrandProductRepository


async def test_create_and_get(db_session: AsyncSession) -> None:
    category = await make_garment_category(db_session)
    repo = BrandProductRepository(db_session)

    size_chart = {
        "S": {"chest_cm": 50, "body_length_cm": 69},
        "M": {"chest_cm": 54, "body_length_cm": 71},
        "L": {"chest_cm": 58, "body_length_cm": 73},
    }
    product = await repo.create(
        brand="J.Crew",
        product_name="Bowery Stretch Oxford",
        product_url="https://www.jcrew.com/p/bowery-stretch-oxford",
        category_id=category.id,
        fabric_composition="98% cotton, 2% elastane",
        stretch_level="slight",
        size_chart=size_chart,
    )

    fetched = await repo.get(product.id)
    assert fetched is not None
    assert fetched.brand == "J.Crew"
    assert fetched.product_url == "https://www.jcrew.com/p/bowery-stretch-oxford"
    assert fetched.size_chart == size_chart


async def test_get_missing_returns_none(db_session: AsyncSession) -> None:
    repo = BrandProductRepository(db_session)
    assert await repo.get(uuid4()) is None


async def test_list_returns_inserted_rows(db_session: AsyncSession) -> None:
    category = await make_garment_category(db_session)
    await make_brand_product(db_session, category=category)
    await make_brand_product(db_session, category=category)

    repo = BrandProductRepository(db_session)
    assert len(await repo.list()) >= 2


async def test_update_patches_size_chart(db_session: AsyncSession) -> None:
    product = await make_brand_product(db_session)
    repo = BrandProductRepository(db_session)

    new_chart = {"M": {"chest_cm": 55}}
    updated = await repo.update(product.id, size_chart=new_chart, scraper_version="v2")
    assert updated is not None
    assert updated.size_chart == new_chart
    assert updated.scraper_version == "v2"


async def test_delete_removes_row(db_session: AsyncSession) -> None:
    product = await make_brand_product(db_session)
    repo = BrandProductRepository(db_session)

    assert await repo.delete(product.id) is True
    assert await repo.get(product.id) is None


async def test_delete_missing_returns_false(db_session: AsyncSession) -> None:
    repo = BrandProductRepository(db_session)
    assert await repo.delete(uuid4()) is False
