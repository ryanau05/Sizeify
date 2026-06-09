"""Cross-test isolation proof for the ``db_session`` fixture.

The writer test creates a probe table and inserts a row. If isolation works,
the reader test sees neither — the outer transaction was rolled back when
the writer's fixture tore down. ``to_regclass`` is the cheapest way to ask
Postgres "does this relation exist?" without raising on absence.

Tests run in declaration order within a file, so writer always precedes
reader.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_writer_creates_probe_row(db_session: AsyncSession) -> None:
    await db_session.execute(text("CREATE TABLE _iso_probe (id INTEGER PRIMARY KEY)"))
    await db_session.execute(text("INSERT INTO _iso_probe (id) VALUES (1)"))
    count = await db_session.execute(text("SELECT count(*) FROM _iso_probe"))
    assert count.scalar_one() == 1


async def test_reader_sees_no_probe_table(db_session: AsyncSession) -> None:
    exists = await db_session.execute(text("SELECT to_regclass('_iso_probe')"))
    assert exists.scalar_one() is None
