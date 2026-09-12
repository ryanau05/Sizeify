"""Unit tests for ``api.domain.measurements`` — TKT-P1-09.

Driven against the real seeded button-down schema wherever the rule under
test is about button-downs, and against small hand-written schemas where
the rule is about the validator itself (malformed entries, absent bounds).
"""

from __future__ import annotations

from typing import Any

import pytest

from api.domain.measurements import (
    dimension_names,
    parse_dimension_specs,
    synthesize_feedback_text,
    validate_measurements,
)
from api.schemas.closet import MeasurementValue
from api.seeds.garment_categories import _BUTTON_DOWN_MEASUREMENT_SCHEMA

# A complete, in-range button-down measurement set.
VALID: dict[str, MeasurementValue] = {
    "chest": MeasurementValue(value=54.0, unit="cm", source="manual_tape"),
    "body_length": MeasurementValue(value=71.0, unit="cm", source="manual_tape"),
    "shoulder_width": MeasurementValue(value=46.0, unit="cm", source="manual_tape"),
    "sleeve_length": MeasurementValue(value=63.0, unit="cm", source="manual_tape"),
    "neck_circumference": MeasurementValue(value=39.0, unit="cm", source="manual_tape"),
    "cuff_circumference": MeasurementValue(value=23.0, unit="cm", source="manual_tape"),
}


def measurements(**overrides: float) -> dict[str, MeasurementValue]:
    merged = dict(VALID)
    for name, value in overrides.items():
        merged[name] = MeasurementValue(value=value, unit="cm", source="manual_tape")
    return merged


def test_complete_in_range_set_has_no_problems() -> None:
    assert validate_measurements(VALID, _BUTTON_DOWN_MEASUREMENT_SCHEMA) == []


def test_missing_required_dimension_is_reported() -> None:
    incomplete = {k: v for k, v in VALID.items() if k != "neck_circumference"}

    problems = validate_measurements(incomplete, _BUTTON_DOWN_MEASUREMENT_SCHEMA)

    assert [(p.field_path, p.kind) for p in problems] == [(("neck_circumference",), "missing")]


def test_every_missing_dimension_is_reported_at_once() -> None:
    """A six-field form should surface all its errors in one response, not
    one per round trip."""
    problems = validate_measurements({}, _BUTTON_DOWN_MEASUREMENT_SCHEMA)

    assert len(problems) == 6
    assert {p.kind for p in problems} == {"missing"}


@pytest.mark.parametrize(
    ("dimension", "value", "kind"),
    [
        # PRD §5.2 names the chest bounds explicitly: under 35cm or over 80cm.
        ("chest", 34.9, "below_minimum"),
        ("chest", 80.1, "above_maximum"),
        ("body_length", 54.0, "below_minimum"),
        ("cuff_circumference", 30.5, "above_maximum"),
    ],
)
def test_out_of_range_value_is_reported_on_the_value_field(
    dimension: str, value: float, kind: str
) -> None:
    problems = validate_measurements(
        measurements(**{dimension: value}), _BUTTON_DOWN_MEASUREMENT_SCHEMA
    )

    assert [(p.field_path, p.kind) for p in problems] == [((dimension, "value"), kind)]


@pytest.mark.parametrize("value", [35.0, 80.0])
def test_range_bounds_are_inclusive(value: float) -> None:
    """PRD §5.2 describes the prompt as firing *under* 35 and *over* 80, so
    the bounds themselves are acceptable."""
    assert validate_measurements(measurements(chest=value), _BUTTON_DOWN_MEASUREMENT_SCHEMA) == []


def test_unknown_dimension_is_rejected() -> None:
    """A typo'd key must not be stored: the matching engine reads by
    canonical name, so it would be silently ignored forever."""
    submitted = dict(VALID)
    submitted["chset"] = MeasurementValue(value=54.0, unit="cm", source="manual_tape")

    problems = validate_measurements(submitted, _BUTTON_DOWN_MEASUREMENT_SCHEMA)

    assert [(p.field_path, p.kind) for p in problems] == [(("chset",), "unknown_dimension")]
    assert "chest" in problems[0].message  # the message lists the real names


def test_problems_accumulate_across_rules() -> None:
    submitted = {k: v for k, v in VALID.items() if k != "chest"}
    submitted["sleeve_length"] = MeasurementValue(value=200.0, unit="cm", source="manual_tape")
    submitted["nonsense"] = MeasurementValue(value=1.0, unit="cm", source="manual_tape")

    problems = validate_measurements(submitted, _BUTTON_DOWN_MEASUREMENT_SCHEMA)

    assert {p.kind for p in problems} == {"missing", "above_maximum", "unknown_dimension"}


# ---------------------------------------------------------------------------
# Validator behavior independent of the button-down schema.
# ---------------------------------------------------------------------------


def test_optional_dimension_may_be_omitted() -> None:
    schema: dict[str, Any] = {
        "dimensions": [
            {"name": "chest", "required": True, "min_cm": 35, "max_cm": 80},
            {"name": "hem", "required": False, "min_cm": 40, "max_cm": 70},
        ]
    }
    submitted = {"chest": MeasurementValue(value=54.0, unit="cm", source="manual_tape")}

    assert validate_measurements(submitted, schema) == []


def test_dimension_without_bounds_accepts_any_positive_value() -> None:
    schema: dict[str, Any] = {"dimensions": [{"name": "chest", "required": True}]}
    submitted = {"chest": MeasurementValue(value=999.0, unit="cm", source="manual_tape")}

    assert validate_measurements(submitted, schema) == []


def test_empty_schema_rejects_everything_submitted() -> None:
    submitted = {"chest": MeasurementValue(value=54.0, unit="cm", source="manual_tape")}

    problems = validate_measurements(submitted, {"dimensions": []})

    assert [p.kind for p in problems] == ["unknown_dimension"]


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {"dimensions": []},
        {"dimensions": ["not-a-mapping"]},
        {"dimensions": [{"label": "nameless"}]},
        {"dimensions": [{"name": ""}]},
    ],
)
def test_malformed_schema_entries_are_skipped_not_raised(schema: dict[str, Any]) -> None:
    """The schema is data. One bad entry must not take down every closet
    write for the category — it just means that dimension cannot be used."""
    assert parse_dimension_specs(schema) == []
    assert validate_measurements({}, schema) == []


@pytest.mark.parametrize("bound", ["not-a-number", None, True])
def test_non_numeric_bounds_are_treated_as_absent(bound: Any) -> None:
    schema: dict[str, Any] = {
        "dimensions": [{"name": "chest", "required": True, "min_cm": bound, "max_cm": bound}]
    }
    submitted = {"chest": MeasurementValue(value=54.0, unit="cm", source="manual_tape")}

    assert validate_measurements(submitted, schema) == []


def test_integer_bounds_from_jsonb_are_honored() -> None:
    """JSONB round-trips ``35.0`` as an int when it has no fractional part."""
    schema: dict[str, Any] = {
        "dimensions": [{"name": "chest", "required": True, "min_cm": 35, "max_cm": 80}]
    }
    submitted = {"chest": MeasurementValue(value=34.0, unit="cm", source="manual_tape")}

    problems = validate_measurements(submitted, schema)

    assert [p.kind for p in problems] == ["below_minimum"]


# ---------------------------------------------------------------------------
# dimension_names / synthesize_feedback_text (TKT-P1-10).
# ---------------------------------------------------------------------------


def test_dimension_names_follow_schema_order() -> None:
    """Order is the schema's, not sorted — it is the order the guided
    measurement flow (PRD §5.2) walks the user through."""
    assert dimension_names(_BUTTON_DOWN_MEASUREMENT_SCHEMA) == [
        "chest",
        "body_length",
        "shoulder_width",
        "sleeve_length",
        "neck_circumference",
        "cuff_circumference",
    ]


def test_dimension_names_of_a_malformed_schema_is_empty() -> None:
    assert dimension_names({}) == []


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {"dimension": "chest", "verdict": "slightly_tight"},
            "User-added: chest slightly tight",
        ),
        (
            {"dimension": "body_length", "verdict": "preferred"},
            "User-added: body length preferred",
        ),
        (
            {"dimension": "sleeve_length", "verdict": "too_short", "magnitude_cm": 2.5},
            "User-added: sleeve length too short (by 2.5cm)",
        ),
        (
            {"dimension": "chest", "verdict": "too_loose", "use_case": "work"},
            "User-added: chest too loose (for work)",
        ),
        (
            {
                "dimension": "neck_circumference",
                "verdict": "too_tight",
                "magnitude_cm": 1.0,
                "use_case": "layering",
            },
            "User-added: neck circumference too tight (by 1cm, for layering)",
        ),
    ],
)
def test_synthesized_feedback_text(kwargs: dict[str, Any], expected: str) -> None:
    assert synthesize_feedback_text(**kwargs) == expected


def test_synthesized_text_is_never_empty() -> None:
    """The column is non-nullable precisely so it is always readable; the
    synthesized fallback must never degenerate to a bare prefix."""
    text = synthesize_feedback_text("chest", "preferred")

    assert text.startswith("User-added: ")
    assert len(text) > len("User-added: ")


def test_synthesized_magnitude_drops_trailing_zeros() -> None:
    """``2.0`` reads as ``2cm``: the user typed a number, not a float."""
    assert "by 2cm" in synthesize_feedback_text("chest", "too_tight", magnitude_cm=2.0)
    assert "by 1.25cm" in synthesize_feedback_text("chest", "too_tight", magnitude_cm=1.25)


def test_synthesized_text_ignores_an_empty_use_case() -> None:
    assert synthesize_feedback_text("chest", "preferred", use_case="") == (
        "User-added: chest preferred"
    )
