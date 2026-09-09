"""Test fixtures.

Per-test transactional isolation is built on the SQLAlchemy "outer
transaction + SAVEPOINT" recipe:

* ``db_session`` opens a fresh connection, begins an outer transaction, and
  hands a session bound to that connection. ``join_transaction_mode=
  "create_savepoint"`` makes any in-test ``session.commit()`` translate to
  SAVEPOINT RELEASE — the outer transaction stays open. When the fixture
  tears down it rolls the outer transaction back, so every mutation made by
  the test (DDL included; Postgres is fully transactional) is wiped.

* ``client`` overrides the ``get_session`` dependency to yield the same
  ``db_session``, so anything an API handler writes lands in the same
  transaction the test reads from — and is rolled back identically.

Test DB source: the compose Postgres at ``DATABASE_URL`` (see
``infra/docker-compose.yml``). We do not use testcontainers; the dev DB is
already required for migrations and ``uv run`` workflows, and the per-test
rollback means tests don't leave residue.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session
from api.main import create_app
from api.repositories.base import get_engine


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_between_tests() -> AsyncIterator[None]:
    """Drop the engine's connection pool after every test.

    pytest-asyncio gives each test its own event loop. The lru_cache'd
    engine in ``api.repositories.base`` survives across tests, so any
    asyncpg connection it pooled would be bound to a closed loop the next
    time around — and asyncpg's connection close runs into "Event loop is
    closed" during fixture teardown. Disposing forces a fresh pool per
    test; the engine object itself is reusable.
    """
    yield
    await get_engine().dispose()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = get_engine()
    connection = await engine.connect()
    await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await connection.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
