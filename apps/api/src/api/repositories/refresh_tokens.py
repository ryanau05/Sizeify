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
from uuid import UUID

from sqlalchemy import select, update
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

    async def mark_revoked(self, id: UUID) -> None:
        """Set ``revoked_at`` on a specific row. Idempotent — re-marking
        an already-revoked row is a no-op."""
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == id)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self.session.flush()

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        """Revoke every still-active refresh token for ``user_id``.

        The ``revoked_at IS NULL`` predicate makes the operation a no-op
        on already-revoked rows so re-running (e.g. from
        ``DELETE /me``) doesn't bump their timestamps.

        Blast-radius logging would be useful here but mypy stubs only
        expose ``rowcount`` on ``CursorResult``, not the generic
        ``Result`` returned by ``session.execute``. We can re-add a
        ``cast`` when there's an incident-response logger to feed.
        """
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self.session.flush()
