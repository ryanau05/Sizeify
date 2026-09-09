"""CRUD tests for ``UserRepository``.

These also exercise the generic ``Repository`` base; tests on
``GarmentCategoryRepository`` cover the TEXT-PK variant of the same
contract.
"""

from __future__ import annotations

from uuid import uuid4

from _factories import make_user
from sqlalchemy.ext.asyncio import AsyncSession

from api.repositories.users import UserRepository


async def test_create_and_get(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)

    user = await repo.create(email="alice@example.com")
    assert user.id is not None  # Python-side default fires before flush

    fetched = await repo.get(user.id)
    assert fetched is not None
    assert fetched.id == user.id
    assert fetched.email == "alice@example.com"
    # Server-side defaults populated.
    assert fetched.preferred_units == "cm"
    assert fetched.created_at is not None


async def test_get_missing_returns_none(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    assert await repo.get(uuid4()) is None


async def test_list_returns_inserted_rows(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    await make_user(db_session, email="a@example.com")
    await make_user(db_session, email="b@example.com")
    await make_user(db_session, email="c@example.com")

    rows = await repo.list()
    emails = {u.email for u in rows}
    assert {"a@example.com", "b@example.com", "c@example.com"} <= emails


async def test_update_patches_fields(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    user = await repo.create(email="bob@example.com")

    updated = await repo.update(user.id, stated_fit_preference="slim", preferred_units="in")
    assert updated is not None
    assert updated.stated_fit_preference == "slim"
    assert updated.preferred_units == "in"
    # Email untouched.
    assert updated.email == "bob@example.com"


async def test_update_missing_returns_none(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    assert await repo.update(uuid4(), stated_fit_preference="slim") is None


async def test_delete_removes_row(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    user = await repo.create(email="bye@example.com")

    assert await repo.delete(user.id) is True
    assert await repo.get(user.id) is None


async def test_delete_missing_returns_false(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    assert await repo.delete(uuid4()) is False
