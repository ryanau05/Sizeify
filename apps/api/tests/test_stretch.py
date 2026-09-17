"""Unit tests for the v1 stretch-adjustment model (TKT-P1-11)."""

from __future__ import annotations

from enum import StrEnum

import pytest

from api.domain import stretch
from api.domain.stretch import (
    STRETCH_OFFSETS_CM,
    UnknownStretchLevelError,
    effective_measurement,
    stretch_offset_cm,
)
from api.schemas.enums import StretchLevel


def test_offset_table_matches_prd_anchor() -> None:
    # PRD §6.3 anchor: moderate stretch ≈ +2 cm.
    assert STRETCH_OFFSETS_CM[StretchLevel.MODERATE] == 2.0
    assert STRETCH_OFFSETS_CM[StretchLevel.NONE] == 0.0


def test_offsets_increase_monotonically_with_stretch() -> None:
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
def test_effective_measurement_adds_offset(level: StretchLevel, expected: float) -> None:
    assert effective_measurement(107.0, level) == expected


def test_none_level_is_treated_as_no_stretch() -> None:
    assert effective_measurement(100.0, None) == 100.0
    assert stretch_offset_cm(None) == 0.0


def test_accepts_raw_string_value() -> None:
    # StrEnum members serialize to their string value; raw wire strings work.
    assert effective_measurement(100.0, "moderate") == 102.0


def test_unknown_level_raises_typed_error() -> None:
    with pytest.raises(UnknownStretchLevelError):
        effective_measurement(100.0, "extreme")


def test_non_positive_measurement_rejected() -> None:
    with pytest.raises(ValueError):
        effective_measurement(0.0, StretchLevel.NONE)


def test_offset_table_covers_every_stretch_level() -> None:
    """The table must stay exhaustive over ``StretchLevel``.

    ``stretch_offset_cm`` looks the level up directly, so a level added to
    the enum without a tuned offset would reach the matching engine as a
    lookup failure on the recommendation path. This is the guard that keeps
    that unreachable — adding a level means tuning its coefficient in the
    same change, which is the deliberate act PRD §6.3 asks for.
    """
    assert set(STRETCH_OFFSETS_CM) == set(StretchLevel)


def test_offset_table_is_immutable() -> None:
    """Hand-tuned constants, not runtime state.

    The read-only mapping is what makes "no learning loop without scope
    approval" (CLAUDE.md gotcha) a property of the code rather than a
    convention — nothing can quietly fit these from feedback data at
    runtime.
    """
    with pytest.raises(TypeError):
        STRETCH_OFFSETS_CM[StretchLevel.HIGH] = 99.0  # type: ignore[index]


def test_effective_measurement_is_unchanged_for_no_stretch() -> None:
    """``none`` is the identity case — a non-stretch garment wears as measured."""
    for measurement in (35.0, 54.0, 107.5):
        assert effective_measurement(measurement, StretchLevel.NONE) == measurement


def test_offset_is_absolute_not_proportional() -> None:
    """PRD §6.3 specifies an additive adjustment, so the same level adds the
    same centimetres regardless of the measurement's size — a multiplier
    would give a chest far more room than a cuff."""
    small = effective_measurement(23.0, StretchLevel.MODERATE) - 23.0
    large = effective_measurement(107.0, StretchLevel.MODERATE) - 107.0

    assert small == large == 2.0


@pytest.mark.parametrize("measurement", [0.0, -1.0, -54.0])
def test_non_positive_measurements_are_rejected(measurement: float) -> None:
    with pytest.raises(ValueError):
        effective_measurement(measurement, StretchLevel.MODERATE)


@pytest.mark.parametrize("level", ["extreme", "None", "NONE", "", "very stretchy", 5])
def test_stretch_offset_cm_rejects_unknown_levels_directly(level: object) -> None:
    """The typed error is the contract at both entry points, not just via
    ``effective_measurement`` — the matching engine calls this one too."""
    with pytest.raises(UnknownStretchLevelError):
        stretch_offset_cm(level)  # type: ignore[arg-type]


def test_untuned_enum_member_raises_the_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A level in the enum but missing from the table must still raise
    ``UnknownStretchLevelError``, not a bare ``KeyError``.

    ``test_offset_table_covers_every_stretch_level`` keeps this branch
    unreachable in real code, so the only way to exercise it is to widen the
    enum under the module. Worth doing: the matching engine catches the typed
    error on the recommendation path, and a ``KeyError`` escaping there would
    surface as a 500 rather than a handled "bad stretch data" case.
    """

    class WidenedStretchLevel(StrEnum):
        NONE = "none"
        SLIGHT = "slight"
        MODERATE = "moderate"
        HIGH = "high"
        EXTREME = "extreme"  # a level nobody tuned an offset for

    monkeypatch.setattr(stretch, "StretchLevel", WidenedStretchLevel)

    with pytest.raises(UnknownStretchLevelError, match="no tuned offset"):
        stretch_offset_cm("extreme")  # type: ignore[arg-type]

    # The tuned levels still work — the widening broke only the new member.
    assert stretch_offset_cm("moderate") == 2.0  # type: ignore[arg-type]
