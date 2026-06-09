"""Tests for the men's button-down shirt seed and its dimension-weight module.

The unit tests live here (rather than under ``tests/unit/domain/``) for Phase
1 simplicity — the FILE_STRUCTURE.md split into ``unit/`` and
``integration/`` lands when there's enough material to warrant it.
"""

from __future__ import annotations

import math

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.domain.dimension_weights import BUTTON_DOWN_DIMENSION_WEIGHTS
from api.models import GarmentCategory
from api.seeds.garment_categories import (
    MENS_BUTTON_DOWN_SHIRT_ID,
    seed_garment_categories,
)

# PRD §6.4 weight table. Repeated here verbatim (not imported) so a typo in
# ``dimension_weights.py`` cannot pass tests by also corrupting the
# expected values.
EXPECTED_BUTTON_DOWN_WEIGHTS: dict[str, float] = {
    "chest": 0.35,
    "shoulder_width": 0.20,
    "body_length": 0.15,
    "sleeve_length": 0.15,
    "neck_circumference": 0.10,
    "cuff_circumference": 0.05,
}

# PRD §5.1 dimension list for men's button-down shirts.
EXPECTED_DIMENSIONS: frozenset[str] = frozenset(
    {
        "chest",
        "body_length",
        "shoulder_width",
        "sleeve_length",
        "neck_circumference",
        "cuff_circumference",
    }
)


class TestDimensionWeights:
    """``api.domain.dimension_weights`` is the matching engine's single
    source of truth for per-dimension weights; PRD §6.4 fixes the values."""

    def test_matches_prd_section_6_4(self) -> None:
        # Cast through dict() for a value-equality check that tolerates the
        # MappingProxyType wrapper.
        assert dict(BUTTON_DOWN_DIMENSION_WEIGHTS) == EXPECTED_BUTTON_DOWN_WEIGHTS

    def test_weights_sum_to_one(self) -> None:
        # Weighted distance comparability across categories depends on this
        # — see ``dimension_weights.py`` module docstring.
        assert math.isclose(sum(BUTTON_DOWN_DIMENSION_WEIGHTS.values()), 1.0)

    def test_all_weights_positive(self) -> None:
        # A zero or negative weight would silently exclude or invert a
        # dimension's contribution to the match score.
        assert all(w > 0 for w in BUTTON_DOWN_DIMENSION_WEIGHTS.values())


class TestMeasurementSchema:
    """The seeded ``measurement_schema`` JSONB must cover every PRD §5.1
    dimension with cm units and ranges that a tape-measurement validator
    can actually check against (PRD §5.2)."""

    @pytest.fixture
    def seeded_row(self) -> dict[str, object]:
        # Pull the row dict the seed will write — keeps the test
        # independent of DB plumbing.
        from api.seeds.garment_categories import _GARMENT_CATEGORIES

        return next(row for row in _GARMENT_CATEGORIES if row["id"] == MENS_BUTTON_DOWN_SHIRT_ID)

    def test_covers_every_prd_dimension(self, seeded_row: dict[str, object]) -> None:
        schema = seeded_row["measurement_schema"]
        assert isinstance(schema, dict)
        dim_names = {d["name"] for d in schema["dimensions"]}
        assert dim_names == EXPECTED_DIMENSIONS

    def test_dimension_names_match_weight_keys(self, seeded_row: dict[str, object]) -> None:
        # Drift between the schema and the weight table silently degrades
        # recommendations — every dimension referenced by the matching
        # engine must have a corresponding measurement entry.
        schema = seeded_row["measurement_schema"]
        assert isinstance(schema, dict)
        dim_names = {d["name"] for d in schema["dimensions"]}
        assert dim_names == set(BUTTON_DOWN_DIMENSION_WEIGHTS.keys())

    def test_all_units_are_cm(self, seeded_row: dict[str, object]) -> None:
        # CLAUDE.md domain conventions: measurements are cm internally.
        schema = seeded_row["measurement_schema"]
        assert isinstance(schema, dict)
        assert all(d["unit"] == "cm" for d in schema["dimensions"])

    def test_chest_range_matches_prd_5_2(self, seeded_row: dict[str, object]) -> None:
        # PRD §5.2: "a chest measurement under 35cm or over 80cm triggers
        # a confirmation prompt".
        schema = seeded_row["measurement_schema"]
        assert isinstance(schema, dict)
        chest = next(d for d in schema["dimensions"] if d["name"] == "chest")
        assert chest["min_cm"] == 35.0
        assert chest["max_cm"] == 80.0

    def test_ranges_are_well_formed(self, seeded_row: dict[str, object]) -> None:
        schema = seeded_row["measurement_schema"]
        assert isinstance(schema, dict)
        for d in schema["dimensions"]:
            assert d["min_cm"] < d["max_cm"], f"{d['name']!r} has min_cm ≥ max_cm"
            assert d["min_cm"] > 0


class TestSeedIntegration:
    """End-to-end against the per-test SAVEPOINT-isolated session
    (``conftest.db_session``). Running the seed twice must leave a single
    row in place — that's the ticket's idempotency acceptance criterion."""

    async def test_seed_creates_one_row(self, db_session: AsyncSession) -> None:
        await seed_garment_categories(db_session)

        row = (
            await db_session.execute(
                select(GarmentCategory).where(GarmentCategory.id == MENS_BUTTON_DOWN_SHIRT_ID)
            )
        ).scalar_one()
        assert row.id == MENS_BUTTON_DOWN_SHIRT_ID
        assert row.display_name == "Men's button-down shirt"
        assert row.dimension_weights == EXPECTED_BUTTON_DOWN_WEIGHTS

    async def test_seed_is_idempotent(self, db_session: AsyncSession) -> None:
        await seed_garment_categories(db_session)
        await seed_garment_categories(db_session)

        count = (
            await db_session.execute(
                select(func.count())
                .select_from(GarmentCategory)
                .where(GarmentCategory.id == MENS_BUTTON_DOWN_SHIRT_ID)
            )
        ).scalar_one()
        assert count == 1
