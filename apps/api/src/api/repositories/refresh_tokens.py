"""``RefreshToken`` repository.

Three typed helpers beyond the generic CRUD — the rotation flow needs
them all:

* ``get_by_hash`` — token lookup via the ``UNIQUE`` index on
  ``token_hash`` (the JWT's SHA-256 hex).
* ``mark_revoked`` — used by ``rotate`` on the successful path.
* ``revoke_all_for_user`` — used by ``rotate`` on the replay path
  (TKT-P1-06 acceptance criterion) and by ``DELETE /me`` (TKT-P1-18).
"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import RefreshToken
from api.repositories.base import Repository


class RefreshTokenRepository(Repository[RefreshToken, UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, RefreshToken)

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Lookup by SHA-256 of the JWS string. Returns ``None`` for an
        unknown hash — used by ``rotate`` to distinguish "this token was
        never issued by us" from "this token was issued and revoked"."""
        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def mark_revoked(self, id: UUID) -> bool:
        """Revoke a specific row. Returns ``True`` iff *this* call revoked it.

        The ``revoked_at IS NULL`` predicate makes this a compare-and-swap,
        and the return value is what makes it usable as one. Two concurrent
        rotations of the same token both read ``revoked_at IS NULL`` under
        READ COMMITTED and both run this UPDATE; the loser matches zero
        rows. Discarding that fact — as this method used to — let both
        callers go on to mint a fresh token pair, so a stolen refresh token
        could be raced into a second, permanent, parallel chain and the
        replay alarm never fired.
        """
        result = await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == id)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self.session.flush()
        # ``rowcount`` lives on ``CursorResult`` in the stubs while
        # ``session.execute`` is typed as the generic ``Result``; DML always
        # returns the former at runtime.
        return bool(cast(CursorResult[Any], result).rowcount)

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        """Revoke every still-active refresh token for ``user_id``.

        The ``revoked_at IS NULL`` predicate makes the operation a no-op
        on already-revoked rows so re-running (e.g. from
        ``DELETE /me``) doesn't bump their timestamps.

        Blast-radius logging would be useful here, and is now only a
        matter of wanting it: ``mark_revoked`` above does the
        ``cast(CursorResult[Any], ...)`` that reaches ``rowcount``, so the
        stub limitation this comment used to cite as the blocker is gone.
        What is still missing is an incident-response logger to feed.
        """
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self.session.flush()
