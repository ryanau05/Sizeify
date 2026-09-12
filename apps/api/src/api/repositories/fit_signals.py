"""``FitSignal`` repository.

Signals attach to ``owned_garment`` rows and cascade with them — the FK
``ON DELETE CASCADE`` is configured in migration 0001, so deleting a
garment through ``OwnedGarmentRepository.delete`` also wipes its signals
without an explicit second call here.
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import FitSignal, OwnedGarment
from api.repositories.base import Repository


class FitSignalRepository(Repository[FitSignal, UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, FitSignal)

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        include_deleted_garments: bool = True,
        limit: int | None = None,
    ) -> list[FitSignal]:
        """Every signal on every garment ``user_id`` owns, oldest first.

        ``fit_signal`` has no ``user_id`` of its own — ownership is one join
        away, through ``owned_garment``. Doing that join here rather than in
        the caller is what keeps "scoped to the owner" a property of the
        repository (see ``OwnedGarmentRepository``) instead of something a
        route has to remember.

        ``include_deleted_garments`` defaults to ``True`` because the
        default caller is the GDPR export (TKT-P1-17), which must report
        every row still held. A UI-facing caller wanting only live garments
        passes ``False``.
        """
        stmt = (
            sa.select(FitSignal)
            .join(OwnedGarment, OwnedGarment.id == FitSignal.owned_garment_id)
            .where(OwnedGarment.user_id == user_id)
        )
        if not include_deleted_garments:
            stmt = stmt.where(OwnedGarment.deleted_at.is_(None))
        stmt = stmt.order_by(FitSignal.created_at, FitSignal.id)
        if limit is not None:
            stmt = stmt.limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())
