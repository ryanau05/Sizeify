"""Migration-chain tests — the acceptance criterion for TKT-P1-01.

Every other test in this suite runs against a database that is *already* at
head, created once by ``alembic upgrade head`` and then held open by the
SAVEPOINT harness in ``conftest.py``. That harness is what makes the rest of
the suite fast, and it is also why nothing here ever re-ran the migration
chain from an empty database. A migration could be edited into a state that
no longer applies and the whole suite would stay green, because the columns
it creates were already there.

That is not hypothetical. ``0003``'s consent backfill was originally
``SET privacy_consent_accepted_at = created_at`` — a column copy, correctly
typed. A later review swapped it for a bound epoch sentinel, which SQLAlchemy
sends as ``$1::VARCHAR`` because the bind's Python value was a ``str``.
Postgres refuses to assign VARCHAR to ``timestamptz``, so ``upgrade head``
aborted on any fresh database. The dev DB was already past ``0003`` and CI had
never run the branch, so nothing noticed.

These tests run the real ``alembic`` CLI against a throwaway database, which
is the only way to exercise what a deploy actually does. They are slow (a few
seconds each) and marked accordingly; CI runs them.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from api.config import get_settings

pytestmark = pytest.mark.slow

#: ``alembic.ini`` lives at the project root, and the CLI resolves
#: ``script_location`` relative to its own cwd.
API_ROOT = Path(__file__).resolve().parents[2]

#: The revision ``upgrade head`` must land on. Hard-coded rather than read back
#: from the scripts so that adding a migration without updating this test is a
#: deliberate act, not a silent one.
HEAD_REVISION = "0005_user_email_ci"

#: The last revision before credentials/consent — the point a pre-auth
#: database would have been sitting at when TKT-P1-07 shipped.
PRE_CREDENTIALS_REVISION = "0002_refresh_tokens"

# Mirrors of the sentinels in ``0003_user_credentials``. Duplicated on purpose:
# importing them would let a change to the migration silently change the
# expectation too, which is the tautology this file exists to avoid.
EXPECTED_LOCKED_HASH = "!locked-no-password-set"
EXPECTED_SYNTHETIC_CONSENT_YEAR = 1970


def _asyncpg_dsn(url: str) -> str:
    """Strip SQLAlchemy's ``+asyncpg`` dialect marker for a raw connection."""
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _alembic(command: str, *args: str, url: str, allow_destructive: bool = False) -> str:
    """Run the real alembic CLI against ``url``. Returns combined output.

    Deliberately a subprocess rather than ``alembic.command.upgrade``: the
    in-process API would share this test's event loop and settings cache, and
    the thing under test is precisely what the deploy command does.
    """
    env = {
        "PATH": "/usr/bin:/bin",
        "DATABASE_URL": url,
        # ``env.py`` builds the URL through Settings, which caches; a fresh
        # process is the simplest way to guarantee it reads ours.
        "JWT_SECRET": "migration-test-secret",
    }
    if allow_destructive:
        env["ALEMBIC_ALLOW_DESTRUCTIVE_DOWNGRADE"] = "1"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", command, *args],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return result.stdout + result.stderr


@pytest_asyncio.fixture
async def migration_db() -> AsyncIterator[str]:
    """An empty database, dropped again afterwards.

    Named off the configured database so it lands on the same server with the
    same credentials, and suffixed so it can never collide with the real one.
    """
    settings_url = get_settings().database_url
    base, _, name = settings_url.rpartition("/")
    throwaway = f"{name}_migchain"
    admin_dsn = _asyncpg_dsn(f"{base}/postgres")

    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{throwaway}"')
        await conn.execute(f'CREATE DATABASE "{throwaway}"')
    finally:
        await conn.close()

    try:
        yield f"{base}/{throwaway}"
    finally:
        conn = await asyncpg.connect(admin_dsn)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{throwaway}" WITH (FORCE)')
        finally:
            await conn.close()


async def _scalar(url: str, sql: str) -> object:
    conn = await asyncpg.connect(_asyncpg_dsn(url))
    try:
        return await conn.fetchval(sql)
    finally:
        await conn.close()


async def test_upgrade_head_applies_to_an_empty_database(migration_db: str) -> None:
    """The deploy path: empty database to head in one command.

    This is the test that fails when a migration is edited into a state that
    only works on a database which already has the schema.
    """
    output = _alembic("upgrade", "head", url=migration_db)

    version = await _scalar(migration_db, "SELECT version_num FROM alembic_version")
    assert version == HEAD_REVISION, f"did not reach head:\n{output}"


async def test_consent_backfill_types_survive_a_populated_table(migration_db: str) -> None:
    """``0003`` backfills a row that predates credentials, without a type error.

    The regression guard. A ``str`` bound against the ``timestamptz`` column
    raises ``DatatypeMismatchError`` here and nowhere else — an empty table
    still plans the UPDATE, but this is the case an operator actually hits.
    """
    _alembic("upgrade", PRE_CREDENTIALS_REVISION, url=migration_db)

    conn = await asyncpg.connect(_asyncpg_dsn(migration_db))
    try:
        await conn.execute(
            'INSERT INTO "user" (id, email, created_at, preferred_units) '
            "VALUES (gen_random_uuid(), 'legacy@example.com', now(), 'cm')"
        )
    finally:
        await conn.close()

    output = _alembic("upgrade", "head", url=migration_db)

    version = await _scalar(migration_db, "SELECT version_num FROM alembic_version")
    assert version == HEAD_REVISION, f"backfill did not survive a populated table:\n{output}"

    conn = await asyncpg.connect(_asyncpg_dsn(migration_db))
    try:
        row = await conn.fetchrow('SELECT password_hash, privacy_consent_accepted_at FROM "user"')
    finally:
        await conn.close()

    assert row is not None
    # Not a valid argon2 PHC string, so nothing can authenticate as this row.
    assert row["password_hash"] == EXPECTED_LOCKED_HASH
    # Greppable as manufactured: this product did not exist in 1970.
    assert row["privacy_consent_accepted_at"].year == EXPECTED_SYNTHETIC_CONSENT_YEAR


async def test_downgrade_refuses_without_the_destructive_opt_in(migration_db: str) -> None:
    """``0003``'s downgrade drops credential columns, so it demands the opt-in."""
    _alembic("upgrade", "head", url=migration_db)

    output = _alembic("downgrade", PRE_CREDENTIALS_REVISION, url=migration_db)

    assert "ALEMBIC_ALLOW_DESTRUCTIVE_DOWNGRADE" in output
    version = await _scalar(migration_db, "SELECT version_num FROM alembic_version")
    assert version == HEAD_REVISION, "a refused downgrade must leave the schema untouched"


async def test_full_downgrade_and_re_upgrade_cycle(migration_db: str) -> None:
    """head → base → head. The other half of the TKT-P1-01 acceptance criterion."""
    _alembic("upgrade", "head", url=migration_db)

    down = _alembic("downgrade", "base", url=migration_db, allow_destructive=True)
    remaining = await _scalar(
        migration_db,
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'user'",
    )
    assert remaining == 0, f"downgrade base left the user table behind:\n{down}"

    up = _alembic("upgrade", "head", url=migration_db)
    version = await _scalar(migration_db, "SELECT version_num FROM alembic_version")
    assert version == HEAD_REVISION, f"re-upgrade after a full downgrade failed:\n{up}"
