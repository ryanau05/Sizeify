"""``OwnedGarment`` repository.

Per-user, per-category list queries (the matching engine's hot read) land
as typed helpers in the tickets that need them — TKT-P1-12 (fit profile
construction) and TKT-P1-09 (closet CRUD endpoints). The §8.2 composite
index on ``(user_id, category_id)`` is already in place.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.models import OwnedGarment
from api.repositories.base import Repository


class OwnedGarmentRepository(Repository[OwnedGarment, UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OwnedGarment)
