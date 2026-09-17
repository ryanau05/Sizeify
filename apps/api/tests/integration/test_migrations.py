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
from sqlalchemy.engine import URL, make_url

from api.config import get_settings

pytestmark = pytest.mark.slow

#: ``alembic.ini`` lives at the project root, and the CLI resolves
#: ``script_location`` relative to its own cwd.
API_ROOT = Path(__file__).resolve().parents[2]

#: The revision ``upgrade head`` must land on. Hard-coded rather than read back
#: from the scripts so that adding a migration without updating this test is a
#: deliberate act, not a silent one.
HEAD_REVISION = "0006_fit_signal_ix"

#: The last revision before credentials/consent — the point a pre-auth
#: database would have been sitting at when TKT-P1-07 shipped.
PRE_CREDENTIALS_REVISION = "0002_refresh_tokens"

# Mirrors of the sentinels in ``0003_user_credentials``. Duplicated on purpose:
# importing them would let a change to the migration silently change the
# expectation too, which is the tautology this file exists to avoid.
EXPECTED_LOCKED_HASH = "!locked-no-password-set"
EXPECTED_SYNTHETIC_CONSENT_YEAR = 1970


def _asyncpg_dsn(url: URL) -> str:
    """A raw libpq DSN for ``url``, without SQLAlchemy's dialect marker."""
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


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


#: Suffix that marks a database as this module's to destroy. Asserted before
#: any DDL runs — see ``migration_db``.
THROWAWAY_SUFFIX = "_migchain"


@pytest_asyncio.fixture
async def migration_db() -> AsyncIterator[str]:
    """An empty database, dropped again afterwards.

    Named off the configured database so it lands on the same server with the
    same credentials, and suffixed so it can never collide with the real one.

    The name is derived with ``make_url``, not string surgery. An earlier
    version did ``database_url.rpartition("/")``, which is not URL parsing: for
    a URL carrying a query string — ``?ssl=require``, the norm on RDS, Cloud
    SQL, Neon and Supabase — the suffix landed inside the *query string*
    instead of the database name::

        .../sizeify?ssl=require  ->  .../sizeify?ssl=require_migchain

    ``CREATE DATABASE`` then made an unrelated empty database while the URL
    handed to the tests still resolved to ``sizeify`` — the real one — and
    ``test_full_downgrade_and_re_upgrade_cycle`` ran ``alembic downgrade base``
    against it with ``ALEMBIC_ALLOW_DESTRUCTIVE_DOWNGRADE=1`` set, dropping
    every table, and still asserted green. The suffix was doing all the safety
    work and one query parameter defeated it.

    So the suffix is no longer trusted to be safe by construction: it is
    checked, against the parsed database name, before a single statement runs.
    """
    configured = make_url(get_settings().database_url)
    original = configured.database
    assert original, f"DATABASE_URL names no database: {configured.render_as_string()}"

    throwaway_url = configured.set(database=f"{original}{THROWAWAY_SUFFIX}")
    throwaway = throwaway_url.database
    assert throwaway and throwaway.endswith(THROWAWAY_SUFFIX) and throwaway != original, (
        f"refusing to run destructive migrations against {throwaway!r} — "
        f"it is not a {THROWAWAY_SUFFIX} database"
    )

    admin_dsn = _asyncpg_dsn(configured.set(database="postgres"))

    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{throwaway}"')
        await conn.execute(f'CREATE DATABASE "{throwaway}"')
    finally:
        await conn.close()

    try:
        yield throwaway_url.render_as_string(hide_password=False)
    finally:
        conn = await asyncpg.connect(admin_dsn)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{throwaway}" WITH (FORCE)')
        finally:
            await conn.close()


async def _scalar(url: str, sql: str) -> object:
    conn = await asyncpg.connect(_asyncpg_dsn(make_url(url)))
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

    conn = await asyncpg.connect(_asyncpg_dsn(make_url(migration_db)))
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

    conn = await asyncpg.connect(_asyncpg_dsn(make_url(migration_db)))
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


@pytest.mark.parametrize(
    "database_url",
    [
        # The shape that defeated the original string-surgery derivation:
        # a query string is the norm on RDS, Cloud SQL, Neon and Supabase.
        "postgresql+asyncpg://u:p@db.example.com:5432/sizeify?ssl=require",
        "postgresql+asyncpg://u:p@db.example.com:5432/sizeify?sslmode=require&foo=bar",
        "postgresql+asyncpg://u:p@localhost:5432/sizeify",
    ],
)
def test_throwaway_database_is_never_the_configured_one(database_url: str) -> None:
    """The name this module destroys must be a ``_migchain`` database.

    ``migration_db`` runs ``alembic downgrade base`` with the destructive
    opt-in set, so the derived name is the only thing standing between these
    tests and whatever ``DATABASE_URL`` points at. Deriving it with
    ``rpartition("/")`` put the suffix in the query string of any URL carrying
    one, leaving the yielded URL resolving to the real database while
    ``CREATE DATABASE`` made an unrelated empty one — and the cycle test still
    passed.
    """
    configured = make_url(database_url)
    throwaway = configured.set(database=f"{configured.database}{THROWAWAY_SUFFIX}")

    assert throwaway.database != configured.database
    assert throwaway.database.endswith(THROWAWAY_SUFFIX)
    # The suffix must be on the database, not smuggled into the query string.
    assert throwaway.query == configured.query
    assert make_url(throwaway.render_as_string(hide_password=False)).database == (
        f"{configured.database}{THROWAWAY_SUFFIX}"
    )
