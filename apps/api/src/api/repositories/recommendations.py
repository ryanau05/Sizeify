"""``Recommendation`` repository.

A typed ``create_from(recommend_output, user_id, brand_product_id)`` helper
that enforces the four PRD §5.4 mandatory components plus the
``prompt_version`` invariant lands in TKT-P1-16 — generic ``create`` is
enough for repository scaffolding.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.models import Recommendation
from api.repositories.base import Repository


class RecommendationRepository(Repository[Recommendation, UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Recommendation)
