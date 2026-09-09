"""``FitSignal`` repository.

Signals attach to ``owned_garment`` rows and cascade with them — the FK
``ON DELETE CASCADE`` is configured in migration 0001, so deleting a
garment through ``OwnedGarmentRepository.delete`` also wipes its signals
without an explicit second call here.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.models import FitSignal
from api.repositories.base import Repository


class FitSignalRepository(Repository[FitSignal, UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, FitSignal)
