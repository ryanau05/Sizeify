"""Tests for the refresh-token retention task.

The table gained a row on every login and every rotation and never lost
one — ``mark_revoked`` and ``revoke_all_for_user`` only stamp
``revoked_at``. These pin both halves of the fix: that expired rows go,
and that the rows a replay depends on stay.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from _factories import make_user
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import RefreshToken
from api.repositories.refresh_tokens import RefreshTokenRepository

TTL_SECONDS = 30 * 24 * 60 * 60


async def _token(
    session: AsyncSession, user_id: UUID, *, age_days: float, revoked: bool = False
) -> RefreshToken:
    issued = datetime.now(UTC) - timedelta(days=age_days)
    row = RefreshToken(
        id=uuid4(),
        token_hash=uuid4().hex,
        user_id=user_id,
        issued_at=issued,
        revoked_at=issued if revoked else None,
    )
    session.add(row)
    await session.flush()
    return row


async def test_expired_rows_are_deleted(db_session: AsyncSession) -> None:
    """Past the refresh TTL a row can no longer change any decision: ``decode``
    rejects the token on ``exp`` before the row is ever consulted."""
    user = await make_user(db_session)
    stale = await _token(db_session, user.id, age_days=31)
    fresh = await _token(db_session, user.id, age_days=1)

    deleted = await RefreshTokenRepository(db_session).delete_expired(ttl_seconds=TTL_SECONDS)

    assert deleted == 1
    remaining = (await db_session.execute(select(RefreshToken.id))).scalars().all()
    assert remaining == [fresh.id]
    assert stale.id not in remaining


async def test_a_revoked_but_unexpired_row_is_kept(db_session: AsyncSession) -> None:
    """The replay alarm depends on it.

    A revoked row inside its TTL is exactly what a stolen token presents.
    Deleting it early would turn a *detected replay* — which revokes the
    user's whole chain — into an ordinary "not recognized" 401, losing the
    signal that a token was stolen.
    """
    user = await make_user(db_session)
    revoked = await _token(db_session, user.id, age_days=2, revoked=True)

    deleted = await RefreshTokenRepository(db_session).delete_expired(ttl_seconds=TTL_SECONDS)

    assert deleted == 0
    assert (await db_session.get(RefreshToken, revoked.id)) is not None


async def test_pruning_is_scoped_to_age_not_to_user(db_session: AsyncSession) -> None:
    """Retention is a property of the row's age, not of whose row it is."""
    alice = await make_user(db_session, email="prune-alice@example.com")
    bob = await make_user(db_session, email="prune-bob@example.com")
    await _token(db_session, alice.id, age_days=40)
    await _token(db_session, bob.id, age_days=40)
    await _token(db_session, bob.id, age_days=1)

    deleted = await RefreshTokenRepository(db_session).delete_expired(ttl_seconds=TTL_SECONDS)

    assert deleted == 2
    surviving = (
        await db_session.execute(select(func.count()).select_from(RefreshToken))
    ).scalar_one()
    assert surviving == 1
