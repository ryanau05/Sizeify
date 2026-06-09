"""``GarmentCategory`` repository.

Categories use a TEXT slug (e.g. ``'mens_button_down_shirt'``) as their
primary key — see ``api.models.garment_category`` for the rationale.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from api.models import GarmentCategory
from api.repositories.base import Repository


class GarmentCategoryRepository(Repository[GarmentCategory, str]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, GarmentCategory)
