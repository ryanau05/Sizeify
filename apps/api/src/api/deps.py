"""FastAPI request-scoped dependencies.

Two layers:

* ``get_session`` yields a per-request ``AsyncSession``. **Only the repository
  layer and the migrations env touch this directly** — route handlers depend
  on the repository providers below, never on ``get_session``.
* ``get_*_repository`` builds a per-entity repository bound to the current
  request's session. FastAPI caches dependency results within a request, so
  multiple repos in one handler share the same session — multi-repo writes
  wrapped in ``transaction(session)`` land atomically.

The ``*Dep`` ``Annotated`` aliases are the canonical call-site form::

    @router.get("/users/{user_id}")
    async def get_user(user_id: UUID, repo: UserRepositoryDep) -> ...:
        ...

The current-user resolver lands alongside these in TKT-P1-08.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.repositories import (
    BrandProductRepository,
    FitSignalRepository,
    GarmentCategoryRepository,
    OwnedGarmentRepository,
    RecommendationRepository,
    RefreshTokenRepository,
    UserRepository,
)
from api.repositories.base import get_sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a per-request ``AsyncSession``, rolling back on error.

    The session is closed at request end. Any active transaction is rolled
    back if the handler raises; commit is the caller's responsibility (via
    ``api.repositories.base.transaction`` or an explicit ``session.commit()``
    inside the repository layer).
    """
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# Internal alias — route handlers should NOT use this directly. Repository
# providers below depend on it; everything else goes through them.
_SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_user_repository(session: _SessionDep) -> UserRepository:
    return UserRepository(session)


async def get_garment_category_repository(
    session: _SessionDep,
) -> GarmentCategoryRepository:
    return GarmentCategoryRepository(session)


async def get_owned_garment_repository(
    session: _SessionDep,
) -> OwnedGarmentRepository:
    return OwnedGarmentRepository(session)


async def get_fit_signal_repository(session: _SessionDep) -> FitSignalRepository:
    return FitSignalRepository(session)


async def get_brand_product_repository(
    session: _SessionDep,
) -> BrandProductRepository:
    return BrandProductRepository(session)


async def get_recommendation_repository(
    session: _SessionDep,
) -> RecommendationRepository:
    return RecommendationRepository(session)


async def get_refresh_token_repository(
    session: _SessionDep,
) -> RefreshTokenRepository:
    return RefreshTokenRepository(session)


# Annotated aliases for route handlers. Importing these instead of the
# ``get_*`` callables keeps signatures readable and consistent.
UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]
GarmentCategoryRepositoryDep = Annotated[
    GarmentCategoryRepository, Depends(get_garment_category_repository)
]
OwnedGarmentRepositoryDep = Annotated[OwnedGarmentRepository, Depends(get_owned_garment_repository)]
FitSignalRepositoryDep = Annotated[FitSignalRepository, Depends(get_fit_signal_repository)]
BrandProductRepositoryDep = Annotated[BrandProductRepository, Depends(get_brand_product_repository)]
RecommendationRepositoryDep = Annotated[
    RecommendationRepository, Depends(get_recommendation_repository)
]
RefreshTokenRepositoryDep = Annotated[RefreshTokenRepository, Depends(get_refresh_token_repository)]
