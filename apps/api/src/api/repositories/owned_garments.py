"""``OwnedGarment`` repository.

Every helper here takes ``user_id`` and filters on it. That is deliberate:
the closet is per-user data, and making "scoped to the owner" the only
available shape means a route handler cannot forget the check. The
generic ``get`` / ``list`` / ``delete`` inherited from ``Repository`` are
unscoped and should not be used for closet reads — prefer ``get_for_user``
and ``list_for_user``.

Soft delete: ``deleted_at`` (migration 0004) tombstones a garment so
historic recommendations that cite it stay interpretable. Reads exclude
tombstones unless asked otherwise, which is why ``include_deleted`` is an
explicit keyword rather than something a caller can pass by accident.
"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import OwnedGarment
from api.repositories.base import Repository


class OwnedGarmentRepository(Repository[OwnedGarment, UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OwnedGarment)

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        category_id: str | None = None,
        include_deleted: bool = False,
        limit: int | None = 100,
        offset: int = 0,
    ) -> list[OwnedGarment]:
        """One user's closet, oldest first.

        Ordered by ``created_at`` so the list is stable across requests —
        an unordered page would shuffle under the client on every poll.
        ``id`` breaks ties, since ``created_at`` has only microsecond
        resolution and a bulk onboarding import can land several garments
        inside one tick.

        ``category_id`` narrows to the §8.2 composite index; TKT-P1-12's
        fit-profile build is its intended caller.

        ``limit=None`` fetches every row. Only the GDPR export (TKT-P1-17)
        should ask for that: an export that silently stopped at 100 rows
        would be a compliance bug, not a paging inconvenience.
        """
        stmt = sa.select(OwnedGarment).where(OwnedGarment.user_id == user_id)
        if category_id is not None:
            stmt = stmt.where(OwnedGarment.category_id == category_id)
        if not include_deleted:
            stmt = stmt.where(OwnedGarment.deleted_at.is_(None))
        stmt = stmt.order_by(OwnedGarment.created_at, OwnedGarment.id).offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_user(
        self,
        id: UUID,
        user_id: UUID,
        *,
        include_deleted: bool = False,
    ) -> OwnedGarment | None:
        """One garment, but only if ``user_id`` owns it.

        Returns ``None`` both for "no such garment" and for "belongs to
        somebody else", so the route maps them to one indistinguishable
        404 and the endpoint cannot be used to probe which garment ids
        exist.
        """
        stmt = sa.select(OwnedGarment).where(
            OwnedGarment.id == id,
            OwnedGarment.user_id == user_id,
        )
        if not include_deleted:
            stmt = stmt.where(OwnedGarment.deleted_at.is_(None))

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def soft_delete(self, id: UUID, user_id: UUID) -> bool:
        """Tombstone a garment. Returns ``False`` if it wasn't there to delete.

        The ``deleted_at IS NULL`` predicate makes a repeat delete a no-op
        returning ``False`` rather than moving the tombstone's timestamp —
        when the garment left the closet is a fact, not something a retry
        should rewrite.
        """
        result = await self.session.execute(
            sa.update(OwnedGarment)
            .where(
                OwnedGarment.id == id,
                OwnedGarment.user_id == user_id,
                OwnedGarment.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now(UTC))
        )
        await self.session.flush()
        # ``rowcount`` is only on ``CursorResult`` in the mypy stubs, while
        # ``session.execute`` is typed as returning the generic ``Result``.
        # A DML statement always yields the former at runtime (same cast as
        # ``refresh_tokens.revoke_all_for_user`` would need).
        return bool(cast(CursorResult[Any], result).rowcount)
