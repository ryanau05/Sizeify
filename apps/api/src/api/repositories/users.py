"""``User`` repository."""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import User
from api.repositories.base import Repository


class UserRepository(Repository[User, UUID]):
    """Generic CRUD over ``user``, plus the email lookup the auth flow
    (TKT-P1-07) needs and the account erasure ``DELETE /me`` performs
    (TKT-P1-18)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)

    async def get_by_email(self, email: str) -> User | None:
        """Lookup by email, case-insensitively.

        Emails are stored as submitted (the local part is case-sensitive
        per RFC 5321, even though essentially no provider treats it that
        way), but matched case-insensitively so ``Alice@example.com``
        cannot sign up alongside ``alice@example.com``. Signup relies on
        this for its duplicate check; the ``UNIQUE`` index on ``email`` is
        exact-match and so cannot catch case variants on its own.

        Uses ``lower(email) = lower(:email)`` rather than ``ILIKE``: ``_``
        is a legal (and common) email character and an ``ILIKE`` wildcard,
        so ``ILIKE`` would match ``alice_b@`` against ``aliceXb@``.
        """
        result = await self.session.execute(
            select(User).where(func.lower(User.email) == email.lower())
        )
        # ``one_or_none`` rather than ``first``: migration 0005 makes a
        # case-insensitive duplicate impossible, so if one somehow exists
        # the right response is a loud failure, not silently authenticating
        # whichever row the planner happened to return first.
        return result.scalars().one_or_none()

    async def delete_account(self, user_id: UUID) -> bool:
        """Erase a user and everything owned by them (PRD §11, TKT-P1-18).

        One ``DELETE FROM "user"``. Every table that holds this user's data
        reaches it through an ``ON DELETE CASCADE`` foreign key, so Postgres
        removes the lot inside the same statement:

        * ``refresh_token`` → the whole token chain, so no credential
          outlives the account.
        * ``owned_garment`` → and ``fit_signal`` behind it, via a second
          cascade hop.
        * ``recommendation``.

        Deleting the children explicitly in Python would be slower, would
        need its own ordering to respect the FKs, and — the real problem —
        would open a window between the child deletes and the parent delete
        in which a concurrent write could attach a new row to a
        half-deleted account. Letting the constraint do it makes erasure
        atomic by construction. ``tests/test_routes_me_delete.py`` asserts
        the cascade actually holds, so a future table that references
        ``user`` without ``ON DELETE CASCADE`` fails there rather than
        silently orphaning rows.

        The catalog tables (``garment_category``, ``brand_product``) are
        referenced with ``RESTRICT`` in the other direction — they are
        shared state, and erasing an account never touches them.

        Returns ``False`` when no row matched, so a concurrent double
        delete is distinguishable from a real one. The route treats both as
        success: the caller's desired state is "this account is gone".
        """
        result = await self.session.execute(delete(User).where(User.id == user_id))
        await self.session.flush()
        # ``rowcount`` lives on ``CursorResult`` in the stubs while
        # ``session.execute`` is typed as the generic ``Result``; a DML
        # statement always returns the former at runtime.
        return bool(cast(CursorResult[Any], result).rowcount)
