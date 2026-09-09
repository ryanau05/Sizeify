"""Seed ``garment_category`` rows for v1.

v1 ships exactly one category: men's button-down shirts (PRD §3.2 / §5.1).
Adding another category here without a separately approved scope change is
forbidden by CLAUDE.md "v1 scope rules" — every category needs its own
measurement schema, dimension weights, NLP extraction prompt update, and
matching-engine wiring.

Run with::

    uv run python -m api.seeds.garment_categories

The script upserts on the primary key, so re-running is safe and brings
the row's ``measurement_schema`` / ``dimension_weights`` into sync with
the constants defined in this module and in
``api.domain.dimension_weights``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.domain.dimension_weights import BUTTON_DOWN_DIMENSION_WEIGHTS
from api.models import GarmentCategory
from api.repositories.base import get_sessionmaker, transaction

# PRD slug. Stored as ``garment_category.id`` (a TEXT primary key) per PRD §8.
MENS_BUTTON_DOWN_SHIRT_ID = "mens_button_down_shirt"

# Measurement schema for men's button-down shirts (PRD §5.1, §5.2).
#
# Dimension names match the keys in ``BUTTON_DOWN_DIMENSION_WEIGHTS`` — they
# are the single source of truth for the names referenced by fit signals,
# matching, and UI labels. The ``_cm`` suffix is reserved for the
# ``owned_garment.measurements`` JSONB keys (e.g. ``chest_cm``); fit
# dimensions themselves are unitless identifiers.
#
# Ranges are the v1 confirmation-prompt bounds (PRD §5.2). Only the chest
# range is explicit in the PRD; the rest are conservative starting estimates
# wide enough to admit any realistic button-down measurement, narrow enough
# to flag tape-measure misreads (e.g. cm vs inches transposition).
_BUTTON_DOWN_MEASUREMENT_SCHEMA: dict[str, Any] = {
    "dimensions": [
        {
            "name": "chest",
            "label": "Chest",
            "guide": "pit-to-pit, doubled",
            "unit": "cm",
            "min_cm": 35.0,
            "max_cm": 80.0,
            "required": True,
        },
        {
            "name": "body_length",
            "label": "Body length",
            "guide": "from base of collar to hem",
            "unit": "cm",
            "min_cm": 55.0,
            "max_cm": 90.0,
            "required": True,
        },
        {
            "name": "shoulder_width",
            "label": "Shoulder width",
            "guide": "seam to seam across the back",
            "unit": "cm",
            "min_cm": 35.0,
            "max_cm": 60.0,
            "required": True,
        },
        {
            "name": "sleeve_length",
            "label": "Sleeve length",
            "guide": "from shoulder seam to cuff",
            "unit": "cm",
            "min_cm": 50.0,
            "max_cm": 75.0,
            "required": True,
        },
        {
            "name": "neck_circumference",
            "label": "Neck",
            "guide": "around the collar at the top button",
            "unit": "cm",
            "min_cm": 30.0,
            "max_cm": 50.0,
            "required": True,
        },
        {
            "name": "cuff_circumference",
            "label": "Cuff",
            "guide": "around the buttoned cuff",
            "unit": "cm",
            "min_cm": 18.0,
            "max_cm": 30.0,
            "required": True,
        },
    ],
}


# Catalog of rows to seed. A list (not a dict) so ordering is stable for
# diff-friendly DB inspection. Stored as plain dicts rather than ORM
# instances so the upsert builds a clean ``INSERT ... ON CONFLICT`` rather
# than fighting the ORM's identity map.
_GARMENT_CATEGORIES: list[dict[str, Any]] = [
    {
        "id": MENS_BUTTON_DOWN_SHIRT_ID,
        "display_name": "Men's button-down shirt",
        "measurement_schema": _BUTTON_DOWN_MEASUREMENT_SCHEMA,
        # ``MappingProxyType`` is read-only at runtime; copy to a plain dict
        # so SQLAlchemy can serialize it to JSONB without complaining.
        "dimension_weights": dict(BUTTON_DOWN_DIMENSION_WEIGHTS),
    },
]


async def seed_garment_categories(session: AsyncSession) -> None:
    """Upsert every known ``garment_category`` row.

    Idempotent: ``ON CONFLICT (id) DO UPDATE`` updates the row's
    ``display_name``, ``measurement_schema``, and ``dimension_weights`` to
    match this module on every run, so editing constants here + re-running
    the seed brings the DB into sync without a hand-written migration.

    Caller owns the transaction — pass an ``AsyncSession`` already inside a
    ``transaction(session)`` block, or let the ``main()`` wrapper below do
    it for you. Splitting transaction management out lets tests drive this
    against the per-test SAVEPOINT-isolated session in ``conftest.py``.
    """
    for row in _GARMENT_CATEGORIES:
        stmt = (
            pg_insert(GarmentCategory)
            .values(**row)
            .on_conflict_do_update(
                index_elements=[GarmentCategory.id],
                set_={
                    "display_name": row["display_name"],
                    "measurement_schema": row["measurement_schema"],
                    "dimension_weights": row["dimension_weights"],
                },
            )
        )
        await session.execute(stmt)


async def _amain() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, transaction(session):
        await seed_garment_categories(session)


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
