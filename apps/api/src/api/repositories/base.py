"""Async DB plumbing + the generic repository base.

Two concerns live here:

* **Engine / session factory** (``get_engine``, ``get_sessionmaker``,
  ``transaction``). One process-wide async engine so the asyncpg connection
  pool is shared across requests — spinning one up per request would eat
  the §9.2 latency budget.
* **``Repository``** — a small generic CRUD base. Per-entity repositories
  in this package subclass it and (optionally) add typed helpers. Route
  handlers depend on those repositories via ``api.deps`` and **never**
  touch ``AsyncSession`` directly (CLAUDE.md backend rule).

Mutating ``Repository`` methods flush so the returned entity has its
primary key populated, but **do not commit** — the caller wraps its unit
of work in ``transaction(session)`` (or commits explicitly) so multi-repo
writes within one request land atomically.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.config import get_settings
from api.models import Base


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    return create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        class_=AsyncSession,
    )


#: Key under which ``transaction`` tracks its own nesting depth on the session.
_DEPTH_KEY = "api.repositories.base.transaction_depth"


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Run a unit of work inside a DB transaction.

    Commits on clean exit; rolls back if the block raises. Callers that
    mutate state are expected to wrap their work in this helper instead of
    calling ``session.commit()`` directly so the rollback path stays uniform.

    Deliberately does **not** call ``session.begin()``. SQLAlchemy autobegins
    a transaction on the session's first statement, so an explicit ``begin()``
    raises "a transaction is already begun" whenever the caller read something
    before deciding to write — which is every read-then-write handler
    (``POST /auth/login`` loads the user, verifies the password, then issues
    tokens).

    Nesting
    -------
    An inner block opens a SAVEPOINT instead of committing, so it can fail and
    roll back its own work while the outer block carries on, and only the
    outermost exit commits. Without that, an inner block's exit committed and
    *ended* the outer transaction; statements after it autobegan a new one, so
    an outer failure rolled back only the tail while the inner work stayed
    permanently committed. Nothing nests today — the Phase 6 share-sheet path
    (profile build, recommendation persist, push dispatch) is what will.

    Depth is tracked explicitly on ``session.info`` rather than inferred from
    ``session.in_transaction()``, because autobegin makes that true as soon as
    the caller reads anything. ``PATCH /closet/garments/{id}`` loads the
    garment before deciding to write, so an inferred check would mistake the
    outermost block for a nested one and never commit it.
    """
    depth: int = session.info.get(_DEPTH_KEY, 0)
    session.info[_DEPTH_KEY] = depth + 1
    try:
        if depth:
            async with session.begin_nested():
                yield session
        else:
            try:
                yield session
                # Inside the try: a failure *during* commit must roll back too,
                # rather than relying on ``deps.get_session`` two layers up.
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    finally:
        session.info[_DEPTH_KEY] = depth


class Repository[EntityT: Base, IdT]:
    """Generic async CRUD over a single SQLAlchemy entity.

    Subclasses bind ``EntityT`` to a concrete model and ``IdT`` to its
    primary-key type, and pass the model class through to ``super().__init__``::

        class UserRepository(Repository[User, UUID]):
            def __init__(self, session: AsyncSession) -> None:
                super().__init__(session, User)

    The constructor is session-only so per-entity repositories slot into the
    FastAPI dependency graph as ``Depends(get_*_repository)`` without leaking
    the session into call sites.
    """

    def __init__(self, session: AsyncSession, model: type[EntityT]) -> None:
        self._session = session
        self._model = model

    @property
    def session(self) -> AsyncSession:
        """Underlying session — exposed so typed-helper methods on
        subclasses can build their own ``select()`` statements without
        reaching past ``self._session`` from outside the package."""
        return self._session

    @property
    def model(self) -> type[EntityT]:
        return self._model

    async def get(self, id: IdT) -> EntityT | None:
        """Fetch by primary key. Returns ``None`` if no row matches."""
        return await self._session.get(self._model, id)

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[EntityT]:
        """Page of rows in insertion-order-ish (no explicit ORDER BY).

        Subclasses that need filtered or sorted lists add typed helpers
        rather than overloading this method — keeps the generic surface
        small and obvious.
        """
        result = await self._session.execute(sa.select(self._model).limit(limit).offset(offset))
        return list(result.scalars().all())

    async def create(self, **fields: Any) -> EntityT:
        """Insert a row, flush, and return the persisted entity (with PK).

        ``**fields`` is intentionally untyped — generic CRUD trades static
        safety for one-line per-entity files. Per-entity repositories
        layer typed creators on top when call sites need them.
        """
        entity = self._model(**fields)
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def update(self, id: IdT, **fields: Any) -> EntityT | None:
        """Patch by primary key. Returns ``None`` if no row matches (so
        route handlers can map missing rows to 404 without a second SELECT).
        """
        entity = await self.get(id)
        if entity is None:
            return None
        for key, value in fields.items():
            setattr(entity, key, value)
        await self._session.flush()
        return entity

    async def delete(self, id: IdT) -> bool:
        """Hard-delete by primary key. Returns ``True`` if a row was
        deleted, ``False`` otherwise.

        Cascade behavior is configured at the schema level (see migration
        0001) — this method just issues the DELETE and lets Postgres
        cascade as declared.
        """
        entity = await self.get(id)
        if entity is None:
            return False
        await self._session.delete(entity)
        await self._session.flush()
        return True
