"""Assemble a user's fit profile from their stored closet.

``domain.fit_profile.build_fit_profile`` is a pure function over a
``ClosetSnapshot``; something has to turn database rows into that snapshot,
and this is it. Keeping the adapter here rather than in the route handler
matters because three callers need the same mapping: ``GET
/closet/fit-profile`` (TKT-P1-13), the Phase 1 exit-criterion test
(TKT-P1-19), and the share-sheet recommendation path (Phase 6). A mapping
that drifts between them would produce different profiles for the same
closet, which is the hardest class of bug to notice.

Only live garments count. A soft-deleted garment is kept so historic
recommendations stay explainable (TKT-P1-09), not so it keeps shaping the
user's current preferences — they removed it from their closet, and the
profile is a statement about what they wear now.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from api.domain.fit_profile import (
    ClosetSnapshot,
    FitProfile,
    GarmentSnapshot,
    SignalSnapshot,
    build_fit_profile,
)
from api.models import FitSignal, OwnedGarment, User
from api.repositories.fit_signals import FitSignalRepository
from api.repositories.owned_garments import OwnedGarmentRepository
from api.schemas.enums import OverallRating, StatedFitPreference, StretchLevel, Verdict
from api.seeds.garment_categories import MENS_BUTTON_DOWN_SHIRT_ID


class MalformedMeasurementError(ValueError):
    """A stored measurement is not the documented ``{value, unit, source}``.

    Typed so the caller can tell "this garment's row is broken" apart from
    any other failure, the same way ``matching.MissingDimensionError`` marks
    a broken size chart.
    """


def _measurements_cm(
    measurements: dict[str, Any], *, garment_id: UUID | None = None
) -> dict[str, float]:
    """``{"chest": {"value": 54.0, "unit": "cm", ...}}`` → ``{"chest": 54.0}``.

    The stored shape keeps each measurement's provenance for PRD §A.9; the
    domain model only needs the number, and it is already in cm (CLAUDE.md —
    conversion happens at the UI boundary, never here).

    Raises on anything that is not that shape. This used to skip quietly,
    which was the worst available option: the garment still counted toward
    ``sample_size`` and maturity but contributed no evidence, so a closet of
    five shirts produced ``maturity: "developing"`` with an empty
    ``dimensions`` map and no hint — a blank profile the user cannot explain
    and the API does not flag. CLAUDE.md's rule about the four-place
    dimension update names this exact failure mode: silently degraded
    recommendations are harder to notice than an error.
    """
    values: dict[str, float] = {}
    for dimension, measurement in measurements.items():
        if not isinstance(measurement, dict) or "value" not in measurement:
            raise MalformedMeasurementError(
                f"garment {garment_id}: measurement {dimension!r} is not a "
                f"{{value, unit, source}} object (got {type(measurement).__name__})"
            )
        raw = measurement["value"]
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise MalformedMeasurementError(
                f"garment {garment_id}: measurement {dimension!r} has a non-numeric value {raw!r}"
            )
        values[dimension] = float(raw)
    return values


def _label_for(garment: OwnedGarment) -> str:
    """How the garment is named back to the user in fit notes and references.

    ``product_name`` when the user gave one, otherwise brand plus size —
    "Your Uniqlo M" reads better than a bare id in a push notification
    (PRD §5.4).
    """
    if garment.product_name:
        return f"{garment.brand} {garment.product_name}"
    return f"{garment.brand} {garment.size_label}"


def _signal_snapshot(signal: FitSignal) -> SignalSnapshot:
    return SignalSnapshot(
        dimension=signal.dimension,
        verdict=Verdict(signal.verdict),
        magnitude_cm=float(signal.magnitude_cm) if signal.magnitude_cm is not None else None,
        use_case=signal.use_case,
    )


def _garment_snapshot(garment: OwnedGarment, signals: list[FitSignal]) -> GarmentSnapshot:
    return GarmentSnapshot(
        label=_label_for(garment),
        brand=garment.brand,
        size_label=garment.size_label,
        measurements_cm=_measurements_cm(garment.measurements, garment_id=garment.id),
        stretch_level=StretchLevel(garment.stretch_level) if garment.stretch_level else None,
        overall_rating=(OverallRating(garment.overall_rating) if garment.overall_rating else None),
        use_cases=tuple(garment.use_cases),
        signals=tuple(_signal_snapshot(signal) for signal in signals),
        garment_id=garment.id,
    )


async def load_closet_snapshot(
    user: User,
    garments: OwnedGarmentRepository,
    signals: FitSignalRepository,
    *,
    category_id: str = MENS_BUTTON_DOWN_SHIRT_ID,
) -> ClosetSnapshot:
    """Read one user's live closet into the domain's input shape.

    Two queries, not one per garment: the signal repository returns the whole
    user's signals in one pass and they are grouped in memory. The share-sheet
    budget allows 100 ms for profile construction (PRD §9.2), which a
    per-garment query would spend on round trips alone.
    """
    rows = await garments.list_for_user(user.id, category_id=category_id, limit=None)
    signal_rows = await signals.list_for_user(user.id, include_deleted_garments=False)

    by_garment: dict[UUID, list[FitSignal]] = defaultdict(list)
    for signal in signal_rows:
        by_garment[signal.owned_garment_id].append(signal)

    return ClosetSnapshot(
        category_id=category_id,
        garments=[_garment_snapshot(row, by_garment[row.id]) for row in rows],
        stated_fit_preference=(
            StatedFitPreference(user.stated_fit_preference) if user.stated_fit_preference else None
        ),
    )


async def build_user_fit_profile(
    user: User,
    garments: OwnedGarmentRepository,
    signals: FitSignalRepository,
    *,
    category_id: str = MENS_BUTTON_DOWN_SHIRT_ID,
) -> FitProfile:
    """Load the closet and run PRD §6.2 construction over it.

    An empty closet is not an error: ``build_fit_profile`` returns a
    cold-start profile with no dimensions, which the endpoint renders as a
    200 carrying the "add a few shirts" hint.
    """
    snapshot = await load_closet_snapshot(user, garments, signals, category_id=category_id)
    return build_fit_profile(snapshot)
