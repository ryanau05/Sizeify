"""CRUD tests for ``UserRepository``.

These also exercise the generic ``Repository`` base; tests on
``GarmentCategoryRepository`` cover the TEXT-PK variant of the same
contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from _factories import TEST_PASSWORD_HASH, make_user
from sqlalchemy.ext.asyncio import AsyncSession

from api.repositories.users import UserRepository

# ``password_hash`` and ``privacy_consent_accepted_at`` are NOT NULL
# (migration 0003), so every direct ``create`` has to supply them. Kept as
# one helper so a future required column is a single edit here.
CONSENT_AT = datetime(2026, 1, 1, tzinfo=UTC)


def user_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "password_hash": TEST_PASSWORD_HASH,
        "privacy_consent_accepted_at": CONSENT_AT,
    }
    fields.update(overrides)
    return fields


async def test_create_and_get(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)

    user = await repo.create(**user_fields(email="alice@example.com"))
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
    user = await repo.create(**user_fields(email="bob@example.com"))

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
    user = await repo.create(**user_fields(email="bye@example.com"))

    assert await repo.delete(user.id) is True
    assert await repo.get(user.id) is None


async def test_delete_missing_returns_false(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    assert await repo.delete(uuid4()) is False


async def test_get_by_email_finds_the_row(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    user = await repo.create(**user_fields(email="carol@example.com"))

    found = await repo.get_by_email("carol@example.com")
    assert found is not None
    assert found.id == user.id


async def test_get_by_email_is_case_insensitive(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    user = await repo.create(**user_fields(email="Carol@Example.com"))

    found = await repo.get_by_email("carol@example.com")
    assert found is not None
    assert found.id == user.id


async def test_get_by_email_does_not_treat_underscore_as_a_wildcard(
    db_session: AsyncSession,
) -> None:
    """``_`` is legal in an email local part and an ``ILIKE`` wildcard —
    matching must be exact-after-lowercasing, not pattern-based."""
    repo = UserRepository(db_session)
    await repo.create(**user_fields(email="carol_b@example.com"))

    assert await repo.get_by_email("carolXb@example.com") is None


async def test_get_by_email_missing_returns_none(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    assert await repo.get_by_email("nobody@example.com") is None


async def test_case_variant_emails_cannot_both_exist(db_session: AsyncSession) -> None:
    """Migration 0005. ``get_by_email`` matches on ``lower(email)`` while the
    plain UNIQUE is exact-match, so without the functional index two rows
    could coexist and login became a coin flip between them."""
    import sqlalchemy.exc

    repo = UserRepository(db_session)
    await repo.create(**user_fields(email="dupe@example.com"))

    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await repo.create(**user_fields(email="DUPE@example.com"))
