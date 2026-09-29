"""Validation of a garment's measurements against its category schema.

Pure: takes a ``garment_category.measurement_schema`` blob and a
submitted measurement mapping, returns the problems. No DB, no HTTP, no
exceptions for control flow — the caller (``api.routes.closet``) turns
the returned list into a 422 body.

Why this isn't a Pydantic validator
-----------------------------------
``measurement_schema`` is a per-category JSONB blob loaded at request
time, so the rules are not knowable when the Pydantic model is defined.
``schemas.closet.GarmentMeasurements`` enforces what *is* static (cm-only
unit, positive value, known ``source``); everything category-specific —
which dimensions exist, which are required, what range each one admits —
lands here.

Adding a dimension means updating all five of ``measurement_schema``,
``dimension_weights``, the NLP extraction prompt, the matching engine, and
``_DIM_LABEL`` in ``domain/recommendation.py`` (CLAUDE.md "Workflow rules").
This module reads the first of those, so it
needs no edit when a dimension is added — that is the point of driving it
from the stored schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from api.schemas.closet import MeasurementValue


@dataclass(frozen=True)
class MeasurementProblem:
    """One thing wrong with a submitted measurement set.

    ``field_path`` is the location *within* the measurements mapping —
    ``("chest",)`` for a missing or unknown dimension, ``("chest",
    "value")`` for one whose number is out of range. The caller prefixes
    the request-body path so the client sees a ``loc`` in the same shape
    Pydantic produces for its own errors.
    """

    field_path: tuple[str, ...]
    message: str
    kind: str


@dataclass(frozen=True)
class DimensionSpec:
    """One dimension as declared by a category's measurement schema."""

    name: str
    required: bool
    min_cm: float | None
    max_cm: float | None
    label: str


def parse_dimension_specs(measurement_schema: Mapping[str, Any]) -> list[DimensionSpec]:
    """Read the ``dimensions`` list out of a category's schema blob.

    Entries without a ``name`` are skipped rather than raising: the schema
    is data, and one malformed entry should not take down every closet
    write for that category. A dimension that never appears in the parsed
    specs simply cannot be submitted (it lands as "unknown dimension"),
    which is a visible, debuggable failure.
    """
    specs: list[DimensionSpec] = []
    for entry in measurement_schema.get("dimensions", []):
        if not isinstance(entry, Mapping):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        specs.append(
            DimensionSpec(
                name=name,
                required=bool(entry.get("required", False)),
                min_cm=_as_float(entry.get("min_cm")),
                max_cm=_as_float(entry.get("max_cm")),
                label=str(entry.get("label", name)),
            )
        )
    return specs


def dimension_names(measurement_schema: Mapping[str, Any]) -> list[str]:
    """Canonical dimension names a category declares, in schema order.

    Used by the fit-signal endpoint (TKT-P1-10), which validates a single
    dimension name rather than a whole measurement set.
    """
    return [spec.name for spec in parse_dimension_specs(measurement_schema)]


def validate_measurements(
    measurements: Mapping[str, MeasurementValue],
    measurement_schema: Mapping[str, Any],
) -> list[MeasurementProblem]:
    """Check a full measurement set against a category schema.

    Returns every problem found, not just the first — a client filling in
    a six-dimension form should see all its errors at once rather than
    discovering them one round trip at a time.

    Three rules, in the order a reader would ask about them:

    1. Every ``required`` dimension is present.
    2. No dimension is submitted that the category does not declare. This
       is strict on purpose: a typo'd key would otherwise be stored
       happily and then silently ignored by the matching engine, which is
       the worst of both worlds — the user believes they entered a
       measurement that never influences a recommendation.
    3. Present values fall inside the declared ``[min_cm, max_cm]`` range
       (PRD §5.2).
    """
    specs = parse_dimension_specs(measurement_schema)
    by_name = {spec.name: spec for spec in specs}
    problems: list[MeasurementProblem] = []

    for spec in specs:
        if spec.required and spec.name not in measurements:
            problems.append(
                MeasurementProblem(
                    field_path=(spec.name,),
                    message=f"Missing required measurement: {spec.label}.",
                    kind="missing",
                )
            )

    for name, measurement in measurements.items():
        matched = by_name.get(name)
        if matched is None:
            problems.append(
                MeasurementProblem(
                    field_path=(name,),
                    message=(
                        f"Unknown measurement dimension {name!r} for this category. "
                        f"Expected one of: {', '.join(sorted(by_name)) or '(none)'}."
                    ),
                    kind="unknown_dimension",
                )
            )
            continue
        problems.extend(_range_problems(matched, measurement))

    return problems


def _range_problems(spec: DimensionSpec, measurement: MeasurementValue) -> list[MeasurementProblem]:
    """Range check for one dimension (PRD §5.2)."""
    problems: list[MeasurementProblem] = []
    if spec.min_cm is not None and measurement.value < spec.min_cm:
        problems.append(
            MeasurementProblem(
                field_path=(spec.name, "value"),
                message=(
                    f"{spec.label} of {measurement.value}cm is below the expected "
                    f"minimum of {spec.min_cm}cm."
                ),
                kind="below_minimum",
            )
        )
    if spec.max_cm is not None and measurement.value > spec.max_cm:
        problems.append(
            MeasurementProblem(
                field_path=(spec.name, "value"),
                message=(
                    f"{spec.label} of {measurement.value}cm is above the expected "
                    f"maximum of {spec.max_cm}cm."
                ),
                kind="above_maximum",
            )
        )
    return problems


def _as_float(value: Any) -> float | None:
    """Coerce a schema bound to a float, or ``None`` if it isn't one.

    Bounds come out of JSONB, so an int, a float, or a missing key are all
    plausible; anything else means the schema row is malformed and the
    bound is treated as absent rather than crashing the request.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def synthesize_feedback_text(
    dimension: str,
    verdict: str,
    *,
    magnitude_cm: float | None = None,
    use_case: str | None = None,
) -> str:
    """Stand-in for the free-text excerpt a manually-entered signal has none of.

    ``fit_signal.raw_feedback_text`` is non-nullable because it is what
    makes extraction quality debuggable — for an NLP-extracted signal it
    holds the user's own words, and comparing those against the structured
    verdict is how prompt regressions get caught (CLAUDE.md domain
    conventions). A user-added signal has no excerpt to store, so rather
    than nulling the column (forbidden) or writing an empty string (which
    would read as "the user said nothing" and pollute the same debugging
    view), it gets a faithful rendering of the structured signal itself.

    The ``User-added:`` prefix is what keeps the two apart when reading the
    column, and it lines up with ``source = 'user_added'``::

        >>> synthesize_feedback_text("chest", "slightly_tight")
        'User-added: chest slightly tight'
        >>> synthesize_feedback_text("sleeve_length", "too_short", magnitude_cm=2.5)
        'User-added: sleeve length too short (by 2.5cm)'
    """
    phrase = f"{dimension.replace('_', ' ')} {verdict.replace('_', ' ')}"
    qualifiers = []
    if magnitude_cm is not None:
        qualifiers.append(f"by {magnitude_cm:g}cm")
    if use_case:
        qualifiers.append(f"for {use_case}")
    if qualifiers:
        phrase = f"{phrase} ({', '.join(qualifiers)})"
    return f"User-added: {phrase}"
