"""Repository layer — the only place that touches ``AsyncSession``.

Route handlers depend on these via ``api.deps`` (FastAPI ``Depends``) and
never import ``sqlalchemy.ext.asyncio`` themselves. CLAUDE.md's backend
rule ("no raw AsyncSession in routes") is enforced both by code review and
by the TKT-P1-20 CI check.
"""

from api.repositories.base import Repository
from api.repositories.brand_products import BrandProductRepository
from api.repositories.fit_signals import FitSignalRepository
from api.repositories.garment_categories import GarmentCategoryRepository
from api.repositories.owned_garments import OwnedGarmentRepository
from api.repositories.recommendations import RecommendationRepository
from api.repositories.refresh_tokens import RefreshTokenRepository
from api.repositories.users import UserRepository

__all__ = [
    "BrandProductRepository",
    "FitSignalRepository",
    "GarmentCategoryRepository",
    "OwnedGarmentRepository",
    "RecommendationRepository",
    "RefreshTokenRepository",
    "Repository",
    "UserRepository",
]
