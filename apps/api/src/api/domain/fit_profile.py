"""Fit-profile construction (PRD §6.2) — TKT-P1-12.

Pure, side-effect-free construction of a user's per-dimension preferred
measurement ranges from a closet snapshot. No DB, no HTTP — the caller hands in
an in-memory ``ClosetSnapshot`` and gets back a ``FitProfile`` that serializes
to ``schemas.fit_profile.FitProfileResponse``.

Model (v1, deliberately simple and documented):

* Each owned garment contributes one evidence point per measurement dimension.
  The garment's flat measurement is first stretch-adjusted (PRD §6.3) so a
  stretchy shirt that fits well implies the user tolerates a larger *effective*
  measurement.
* A fit signal on that dimension shifts the implied preferred value: a garment
  the user found "slightly tight" means they prefer a bit *more* room than that
  garment offered, so its evidence point moves up. Magnitude (cm) is used when
  the signal carries one, otherwise a per-verdict default.
* Evidence points are combined as a rating-weighted mean → ``preferred_cm``.
  ``spread_cm`` reflects both the dispersion of the evidence and how few
  garments contributed (wide with a thin closet, narrowing as it grows — PRD
  §6.2). ``maturity`` follows the PRD's cold-start guidance.

Use-case conditioning (PRD §6.5) is kept minimal here: signals tagged with a
``use_case`` are excluded from the unconditioned profile (the headline demo
flow). The ``use_case_variants`` field is preserved in the wire shape for when
TKT-P1-12's full version lands.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from api.domain.stretch import effective_measurement
from api.schemas.enums import OverallRating, ProfileMaturity, StretchLevel, Verdict
from api.schemas.fit_profile import DimensionPreference, FitProfileResponse

# --- Tunable v1 constants (hand-tuned; see module docstring + PRD §6.2) ------

# How much a garment's evidence counts, by the user's overall rating of it.
RATING_WEIGHTS: Mapping[OverallRating | None, float] = {
    OverallRating.LOVE: 1.0,
    OverallRating.LIKE: 0.75,
    OverallRating.TOLERABLE: 0.45,
    OverallRating.DISLIKE: 0.2,
    None: 0.5,  # unrated garment — moderate, neutral evidence
}

# Signed cm shift applied to a garment's measurement given a verdict on that
# dimension, used only when the signal carries no explicit magnitude. Positive
# = user prefers MORE than this garment offered (it ran tight / short).
_DEFAULT_VERDICT_SHIFT_CM: Mapping[Verdict, float] = {
    Verdict.TOO_TIGHT: 4.0,
    Verdict.SLIGHTLY_TIGHT: 1.5,
    Verdict.PREFERRED: 0.0,
    Verdict.SLIGHTLY_LOOSE: -1.5,
    Verdict.TOO_LOOSE: -4.0,
    Verdict.SLIGHTLY_SHORT: 1.5,
    Verdict.TOO_SHORT: 4.0,
}

# Sign (direction) of each verdict, used to orient an explicit magnitude.
_VERDICT_SIGN: Mapping[Verdict, float] = {
    v: (1.0 if shift > 0 else -1.0 if shift < 0 else 0.0)
    for v, shift in _DEFAULT_VERDICT_SHIFT_CM.items()
}

BASE_SPREAD_CM = 3.0  # one-sigma spread at n=1, shrinks ~1/sqrt(n)
MIN_SPREAD_CM = 1.0  # never claim tighter certainty than this in v1

# Closet-size thresholds for profile maturity (PRD §6.2: min 3, narrows at 15+).
_DEVELOPING_MIN_GARMENTS = 3
_MATURE_MIN_GARMENTS = 15


# --- Input snapshot (what the caller passes in) ------------------------------


@dataclass(frozen=True)
class SignalSnapshot:
    """One fit signal on a single dimension of an owned garment."""

    dimension: str
    verdict: Verdict
    magnitude_cm: float | None = None
    use_case: str | None = None


@dataclass(frozen=True)
class GarmentSnapshot:
    """An owned garment as the matching engine sees it.

    ``measurements_cm`` keys are canonical dimension names
    (``chest``, ``shoulder_width``, …) — the same names used by
    ``dimension_weights`` and the size charts. Normalisation from any
    shorthand happens upstream (the demo seed), not here.
    """

    label: str
    brand: str
    size_label: str
    measurements_cm: Mapping[str, float]
    stretch_level: StretchLevel | None = None
    overall_rating: OverallRating | None = None
    use_cases: Sequence[str] = field(default_factory=tuple)
    signals: Sequence[SignalSnapshot] = field(default_factory=tuple)


@dataclass(frozen=True)
class ClosetSnapshot:
    """The closet the profile is built from."""

    category_id: str
    garments: Sequence[GarmentSnapshot]


# --- Output profile ----------------------------------------------------------


@dataclass(frozen=True)
class DimensionStat:
    """Preferred centre + uncertainty on one dimension (cm)."""

    preferred_cm: float
    spread_cm: float
    sample_size: int


@dataclass(frozen=True)
class ReferenceGarment:
    """A closet garment retained so recommendations can cite it (PRD §5.4)."""

    label: str
    brand: str
    size_label: str
    overall_rating: OverallRating | None
    measurements_cm: Mapping[str, float]
    use_cases: tuple[str, ...]


@dataclass(frozen=True)
class FitProfile:
    """Constructed fit profile — the matching engine's input."""

    category_id: str
    maturity: ProfileMaturity
    sample_size: int
    dimensions: Mapping[str, DimensionStat]
    references: tuple[ReferenceGarment, ...]

    def to_response(self) -> FitProfileResponse:
        """Serialize to the ``GET /closet/fit-profile`` wire shape."""
        hint = (
            "Add at least 3 shirts you already own to get a reliable size."
            if self.maturity is ProfileMaturity.COLD_START
            else None
        )
        return FitProfileResponse(
            category_id=self.category_id,
            maturity=self.maturity,
            sample_size=self.sample_size,
            dimensions={
                name: DimensionPreference(
                    preferred_cm=stat.preferred_cm,
                    spread_cm=stat.spread_cm,
                    sample_size=stat.sample_size,
                )
                for name, stat in self.dimensions.items()
            },
            use_case_variants={},
            hint=hint,
        )


# --- Construction ------------------------------------------------------------


def _maturity_for(count: int) -> ProfileMaturity:
    if count < _DEVELOPING_MIN_GARMENTS:
        return ProfileMaturity.COLD_START
    if count < _MATURE_MIN_GARMENTS:
        return ProfileMaturity.DEVELOPING
    return ProfileMaturity.MATURE


def _signal_shift_cm(signal: SignalSnapshot) -> float:
    """Signed cm shift implied by a fit signal."""
    if signal.magnitude_cm is not None:
        return _VERDICT_SIGN[signal.verdict] * signal.magnitude_cm
    return _DEFAULT_VERDICT_SHIFT_CM[signal.verdict]


def _unconditioned_signal(garment: GarmentSnapshot, dimension: str) -> SignalSnapshot | None:
    """The garment's signal on ``dimension`` that applies to the default profile.

    Use-case-tagged signals are excluded from the unconditioned profile.
    """
    for sig in garment.signals:
        if sig.dimension == dimension and sig.use_case is None:
            return sig
    return None


def build_fit_profile(snapshot: ClosetSnapshot) -> FitProfile:
    """Construct a ``FitProfile`` from a closet snapshot (pure function)."""
    garments = list(snapshot.garments)
    maturity = _maturity_for(len(garments))

    # Gather, per dimension, the (weight, implied_preferred_cm) evidence points.
    evidence: dict[str, list[tuple[float, float]]] = {}
    for garment in garments:
        weight = RATING_WEIGHTS[garment.overall_rating]
        for dim, measured in garment.measurements_cm.items():
            effective = effective_measurement(measured, garment.stretch_level)
            sig = _unconditioned_signal(garment, dim)
            implied = effective + (_signal_shift_cm(sig) if sig else 0.0)
            evidence.setdefault(dim, []).append((weight, implied))

    dimensions: dict[str, DimensionStat] = {}
    for dim, points in evidence.items():
        total_w = sum(w for w, _ in points)
        if total_w <= 0:
            continue
        preferred = sum(w * v for w, v in points) / total_w
        # Dispersion of the evidence around the weighted mean.
        var = sum(w * (v - preferred) ** 2 for w, v in points) / total_w
        dispersion = math.sqrt(var)
        n = len(points)
        spread = max(MIN_SPREAD_CM, dispersion, BASE_SPREAD_CM / math.sqrt(n))
        dimensions[dim] = DimensionStat(
            preferred_cm=round(preferred, 2),
            spread_cm=round(spread, 2),
            sample_size=n,
        )

    references = tuple(
        ReferenceGarment(
            label=g.label,
            brand=g.brand,
            size_label=g.size_label,
            overall_rating=g.overall_rating,
            measurements_cm=dict(g.measurements_cm),
            use_cases=tuple(g.use_cases),
        )
        for g in garments
        if g.overall_rating is not OverallRating.DISLIKE
    )

    return FitProfile(
        category_id=snapshot.category_id,
        maturity=maturity,
        sample_size=len(garments),
        dimensions=dimensions,
        references=references,
    )
