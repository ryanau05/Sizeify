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

    async def count_for_garment(self, owned_garment_id: UUID) -> int:
        """How many fit signals one garment carries.

        Backs the per-garment ceiling in ``create_fit_signal``. A COUNT rather
        than ``len(list_...)`` for the same reason the closet ceiling uses one:
        the number is all the caller wants, and it is served by
        ``ix_fit_signal_owned_garment_id``.
        """
        result = await self.session.execute(
            sa.select(sa.func.count())
            .select_from(FitSignal)
            .where(FitSignal.owned_garment_id == owned_garment_id)
        )
        return result.scalar_one()

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        include_deleted_garments: bool = True,
        limit: int | None = None,
        offset: int = 0,
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

        ``limit``/``offset`` page the result. The ordering is total — a
        ``created_at`` tie breaks on ``id`` — so paging cannot repeat or skip
        a row the way an unordered page would. The GDPR export uses this to
        walk a large account without materializing it in one go.
        """
        stmt = (
            sa.select(FitSignal)
            .join(OwnedGarment, OwnedGarment.id == FitSignal.owned_garment_id)
            .where(OwnedGarment.user_id == user_id)
        )
        if not include_deleted_garments:
            stmt = stmt.where(OwnedGarment.deleted_at.is_(None))
        stmt = stmt.order_by(FitSignal.created_at, FitSignal.id).offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())
