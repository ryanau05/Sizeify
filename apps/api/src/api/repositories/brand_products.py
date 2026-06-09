"""``BrandProduct`` repository.

``product_url`` is the cache key on the share-sheet hot path (PRD §9.2);
``get_by_url`` will land here when the recommend flow needs it. The
unique constraint on ``product_url`` already provides the index.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.models import BrandProduct
from api.repositories.base import Repository


class BrandProductRepository(Repository[BrandProduct, UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, BrandProduct)
