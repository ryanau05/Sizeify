"""``User`` repository."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.models import User
from api.repositories.base import Repository


class UserRepository(Repository[User, UUID]):
    """Generic CRUD over ``user``. Typed helpers (``get_by_email``, etc.)
    land alongside in TKT-P1-07 when the auth flow needs them."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)
