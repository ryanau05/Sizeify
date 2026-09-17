"""Acceptance test for TKT-P1-00b.

Mounts a throwaway route that depends on ``get_session`` and round-trips
``SELECT 1`` against the running Postgres (see ``infra/docker-compose.yml``).
The test fails if compose Postgres isn't reachable — that's intentional and
matches the ticket's acceptance criterion.

A real per-test transactional fixture lands in TKT-P1-00e; this file just
proves the dependency wiring.
"""

import contextlib
from datetime import UTC, datetime
from typing import Annotated

import pytest
from _factories import TEST_PASSWORD_HASH
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session
from api.repositories.base import transaction
from api.repositories.users import UserRepository


def build_probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/_probe/select-1")
    async def probe(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> dict[str, int]:
        result = await session.execute(text("SELECT 1"))
        return {"value": result.scalar_one()}

    return app


@pytest.mark.asyncio
async def test_get_session_round_trips_select_1() -> None:
    app = build_probe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_probe/select-1")

    assert response.status_code == 200
    assert response.json() == {"value": 1}


# ---------------------------------------------------------------------------
# transaction() nesting
# ---------------------------------------------------------------------------


async def test_nested_transaction_rolls_back_with_the_outer_block(
    db_session: AsyncSession,
) -> None:
    """An inner block opens a SAVEPOINT, so an outer failure takes its work
    with it.

    Before this, the inner block's exit committed and *ended* the outer
    transaction; statements after it autobegan a new one, so an outer failure
    rolled back only the tail and the inner write stayed permanently
    committed. ``session.begin()`` used to raise loudly on the nesting;
    autobegin made it silent.
    """
    repo = UserRepository(db_session)

    with pytest.raises(RuntimeError, match="outer failed"):
        async with transaction(db_session):
            await repo.create(**user_fields(email="outer@example.com"))
            async with transaction(db_session):
                await repo.create(**user_fields(email="inner@example.com"))
            raise RuntimeError("outer failed")

    assert await repo.get_by_email("inner@example.com") is None
    assert await repo.get_by_email("outer@example.com") is None


async def test_inner_failure_does_not_take_down_the_outer_block(
    db_session: AsyncSession,
) -> None:
    """The other half of the savepoint contract: an inner block can fail and
    be handled without discarding work the outer block already did."""
    repo = UserRepository(db_session)

    async with transaction(db_session):
        await repo.create(**user_fields(email="kept@example.com"))
        with contextlib.suppress(RuntimeError):
            async with transaction(db_session):
                await repo.create(**user_fields(email="discarded@example.com"))
                raise RuntimeError("inner failed")

    assert await repo.get_by_email("kept@example.com") is not None
    assert await repo.get_by_email("discarded@example.com") is None


async def test_depth_is_restored_after_each_block(db_session: AsyncSession) -> None:
    """Two sequential top-level blocks must both commit, rather than the
    second being treated as nested because the first leaked its depth."""
    repo = UserRepository(db_session)

    async with transaction(db_session):
        await repo.create(**user_fields(email="first@example.com"))
    async with transaction(db_session):
        await repo.create(**user_fields(email="second@example.com"))

    assert await repo.get_by_email("first@example.com") is not None
    assert await repo.get_by_email("second@example.com") is not None


async def test_read_before_the_block_does_not_make_it_look_nested(
    db_session: AsyncSession,
) -> None:
    """SQLAlchemy autobegins on the first statement, so by the time a
    read-then-write handler enters ``transaction`` a transaction is already
    active. Depth is tracked explicitly for exactly this reason — inferring
    nesting from ``in_transaction()`` would mistake the outermost block for an
    inner one and never commit it. ``PATCH /closet/garments/{id}`` does this.
    """
    repo = UserRepository(db_session)
    await repo.get_by_email("nobody@example.com")  # autobegins
    assert db_session.in_transaction()

    async with transaction(db_session):
        await repo.create(**user_fields(email="after-read@example.com"))

    assert await repo.get_by_email("after-read@example.com") is not None


def user_fields(**overrides: object) -> dict[str, object]:
    """``password_hash`` and ``privacy_consent_accepted_at`` are NOT NULL."""
    fields: dict[str, object] = {
        "password_hash": TEST_PASSWORD_HASH,
        "privacy_consent_accepted_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return fields
