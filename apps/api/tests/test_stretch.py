"""Unit tests for the v1 stretch-adjustment model (DEMO-01 / TKT-P1-11)."""

from __future__ import annotations

import pytest

from api.domain.stretch import (
    STRETCH_OFFSETS_CM,
    UnknownStretchLevelError,
    effective_measurement,
    stretch_offset_cm,
)
from api.schemas.enums import StretchLevel


def test_offset_table_matches_prd_anchor():
    # PRD §6.3 anchor: moderate stretch ≈ +2 cm.
    assert STRETCH_OFFSETS_CM[StretchLevel.MODERATE] == 2.0
    assert STRETCH_OFFSETS_CM[StretchLevel.NONE] == 0.0


def test_offsets_increase_monotonically_with_stretch():
    levels = [StretchLevel.NONE, StretchLevel.SLIGHT, StretchLevel.MODERATE, StretchLevel.HIGH]
    offsets = [STRETCH_OFFSETS_CM[lvl] for lvl in levels]
    assert offsets == sorted(offsets)
    assert offsets[0] == 0.0
    assert len(set(offsets)) == len(offsets)  # strictly distinct


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (StretchLevel.NONE, 107.0),
        (StretchLevel.SLIGHT, 108.0),
        (StretchLevel.MODERATE, 109.0),
        (StretchLevel.HIGH, 110.5),
    ],
)
def test_effective_measurement_adds_offset(level, expected):
    assert effective_measurement(107.0, level) == expected


def test_none_level_is_treated_as_no_stretch():
    assert effective_measurement(100.0, None) == 100.0
    assert stretch_offset_cm(None) == 0.0


def test_accepts_raw_string_value():
    # StrEnum members serialize to their string value; raw wire strings work.
    assert effective_measurement(100.0, "moderate") == 102.0


def test_unknown_level_raises_typed_error():
    with pytest.raises(UnknownStretchLevelError):
        effective_measurement(100.0, "extreme")


def test_non_positive_measurement_rejected():
    with pytest.raises(ValueError):
        effective_measurement(0.0, StretchLevel.NONE)
