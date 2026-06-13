"""Fabric-stretch adjustment for the matching engine (PRD §6.3).

When the engine compares a candidate garment's size chart against the user's
fit profile, stretchy fabrics must be made to "fit bigger" than their flat
measurement: a 107 cm chest in a high-stretch knit wears like a larger
non-stretch shirt. We model this as a small additive offset (in cm) applied to
the garment's measured value, keyed on the coarse 4-level ``StretchLevel``.

The coefficients below are **hand-tuned for v1** from public clothing-engineering
references, anchored on the PRD §6.3 example ("effective chest = measured chest
+ 2 cm for moderate stretch"). They are deliberately coarse.

IMPORTANT (CLAUDE.md gotcha / PRD §6.3): these are hand-tuned constants. Do NOT
add a learning loop that fits them from user-feedback data without explicit
scope approval — that is a v2 item and changes the system's calibration story.
"""

from __future__ import annotations

from types import MappingProxyType
from collections.abc import Mapping

from api.schemas.enums import StretchLevel


class UnknownStretchLevelError(ValueError):
    """Raised when a stretch level outside the v1 enum is passed in.

    A typed error (rather than a bare ``KeyError``) so callers in the matching
    engine can distinguish "bad stretch data" from other lookup failures.
    """


# Additive effective-measurement offsets in centimetres, per stretch level.
# none → no adjustment; moderate → +2 cm (PRD §6.3 anchor). slight/high are
# interpolated/extrapolated around that anchor and kept intentionally coarse.
STRETCH_OFFSETS_CM: Mapping[StretchLevel, float] = MappingProxyType(
    {
        StretchLevel.NONE: 0.0,
        StretchLevel.SLIGHT: 1.0,
        StretchLevel.MODERATE: 2.0,
        StretchLevel.HIGH: 3.5,
    }
)


def stretch_offset_cm(stretch_level: StretchLevel | None) -> float:
    """Return the additive cm offset for a stretch level.

    ``None`` (unknown fabric) is treated as ``NONE`` — the conservative choice,
    since assuming stretch the garment may not have would bias the engine toward
    recommending a size too small.
    """
    if stretch_level is None:
        return 0.0
    try:
        return STRETCH_OFFSETS_CM[StretchLevel(stretch_level)]
    except ValueError as exc:  # StretchLevel(...) rejected the value
        raise UnknownStretchLevelError(
            f"unknown stretch level: {stretch_level!r}"
        ) from exc


def effective_measurement(
    measurement_cm: float, stretch_level: StretchLevel | None
) -> float:
    """Adjust a garment measurement for fabric stretch (PRD §6.3).

    ``effective = measured + offset(stretch_level)``. Used by the matching
    engine on each candidate size's chart values before comparing them to the
    user's preferred range.

    Raises ``UnknownStretchLevelError`` if ``stretch_level`` is a non-empty
    value outside the v1 enum.
    """
    if measurement_cm <= 0:
        raise ValueError(f"measurement_cm must be positive, got {measurement_cm!r}")
    return measurement_cm + stretch_offset_cm(stretch_level)
