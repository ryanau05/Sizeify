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

The weak prior
--------------
PRD §6.2 starts the posterior from "a weak prior based on the user's stated
overall preference (slim / regular / relaxed), expressed as broad measurement
ranges". That prior enters as a single low-weight pseudo-observation per
dimension (``PRIOR_WEIGHT``, in the same units as the rating weights above),
so it steers a one-garment closet and is swamped by a mature one — with 15
garments it carries under 1% of the total weight.

In practice it moves ``preferred_cm`` and not ``spread_cm``. The prior does
contribute to the dispersion term, but the gaps between the slim, regular and
relaxed columns below are 2-3 cm, and at that distance the dispersion it adds
stays under the ``BASE_SPREAD_CM / sqrt(n)`` floor for every closet size. It
would take a user whose closet sits ~8 cm from what they told us for the prior
to widen the range — a case worth reporting as uncertainty, and one the model
does handle, but not the ordinary one.

The prior only touches dimensions the closet already has evidence for. It
never invents a dimension, so an empty closet still yields an empty
``dimensions`` map — the contract ``schemas.fit_profile.FitProfileResponse``
documents and TKT-P1-13 depends on. A user who has entered nothing gets a
cold-start hint, not a fabricated six-dimension profile.

Use-case conditioning
---------------------
PRD §6.2: "a sleeve length marked 'preferred' for casual but 'slightly short'
for layering creates a context-dependent preference." Signals carry an
optional ``use_case``; the unconditioned profile in ``dimensions`` uses only
untagged signals, and each use case seen in the closet also gets its own entry
in ``use_case_variants``, built from that use case's signals with the untagged
ones as fallback. The matching engine picks the variant when the share-sheet
URL or the user's history implies an intent (PRD §6.4), and falls back to
``dimensions`` when it does not.

What v1 does not model
----------------------
PRD §6.2 also says a "dislike"-rated garment marked "too tight" *sets a hard
lower bound*. Here that garment still moves the posterior in the right
direction — low rating weight, large positive shift — but no inviolable bound
is stored, because ``DimensionPreference`` is a centre-plus-spread wire shape
with no room for one. Adding true bounds is a wire-format change, not a tweak
to this module.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from uuid import UUID

from api.domain.stretch import effective_measurement
from api.schemas.enums import (
    OverallRating,
    ProfileMaturity,
    StatedFitPreference,
    StretchLevel,
    Verdict,
)
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

# Weight of the stated-preference prior, in the same units as RATING_WEIGHTS
# (where a loved garment counts 1.0). Deliberately below *every* rating weight,
# including a disliked garment's 0.2, so any single measured garment outvotes
# what the user said during onboarding. "Weak" is the PRD's word (§6.2) and
# this is the number that makes it true.
PRIOR_WEIGHT = 0.15

# Per-dimension prior centres in cm, by stated fit preference (PRD §6.2's
# "broad measurement ranges"). Hand-tuned for v1 from typical men's medium
# button-down *garment* measurements — not body measurements — on the same
# scale as ``garment_category.measurement_schema`` (chest is pit-to-pit, so
# ~54 cm, not a doubled circumference).
#
# Slim and relaxed are the regular column shifted by roughly the step between
# adjacent sizes on the dimensions that actually distinguish a cut, which is
# why the neck and cuff move less than the chest and shoulders.
#
# IMPORTANT: hand-tuned, like the stretch coefficients in ``domain.stretch``.
# Do not fit these from user data without explicit scope approval (CLAUDE.md
# gotchas) — a prior learned from the population is a different product
# decision than a prior taken from what this user told us.
PRIOR_MEASUREMENTS_CM: Mapping[StatedFitPreference, Mapping[str, float]] = MappingProxyType(
    {
        StatedFitPreference.SLIM: MappingProxyType(
            {
                "chest": 52.0,
                "body_length": 71.0,
                "shoulder_width": 44.5,
                "sleeve_length": 62.5,
                "neck_circumference": 38.0,
                "cuff_circumference": 22.0,
            }
        ),
        StatedFitPreference.REGULAR: MappingProxyType(
            {
                "chest": 54.0,
                "body_length": 72.0,
                "shoulder_width": 46.0,
                "sleeve_length": 63.0,
                "neck_circumference": 39.0,
                "cuff_circumference": 23.0,
            }
        ),
        StatedFitPreference.RELAXED: MappingProxyType(
            {
                "chest": 57.0,
                "body_length": 74.0,
                "shoulder_width": 48.0,
                "sleeve_length": 64.0,
                "neck_circumference": 40.5,
                "cuff_circumference": 24.5,
            }
        ),
    }
)

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
    shorthand happens upstream (``api.services.fit_profile``), not here.
    """

    label: str
    brand: str
    size_label: str
    measurements_cm: Mapping[str, float]
    stretch_level: StretchLevel | None = None
    overall_rating: OverallRating | None = None
    use_cases: Sequence[str] = field(default_factory=tuple)
    signals: Sequence[SignalSnapshot] = field(default_factory=tuple)
    # ``owned_garment.id``, carried so a persisted recommendation can cite the
    # closet rows that produced it (``recommendation.reference_garment_ids``,
    # TKT-P1-16). Optional because the domain is pure — a hand-built snapshot
    # in a unit test has no database row behind it. Kept last so adding it did
    # not renumber the positional arguments of existing call sites; the field
    # order is pinned by ``test_snapshot_field_order_is_stable``.
    garment_id: UUID | None = None


@dataclass(frozen=True)
class ClosetSnapshot:
    """The closet the profile is built from.

    ``stated_fit_preference`` is the onboarding answer (PRD §10.1 step 3),
    carried here because it is what seeds the weak prior. ``None`` means the
    user skipped it, in which case the profile is evidence-only.
    """

    category_id: str
    garments: Sequence[GarmentSnapshot]
    stated_fit_preference: StatedFitPreference | None = None


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
    # See ``GarmentSnapshot.garment_id``. Last field with a default so the
    # positional construction used across the domain tests keeps working.
    garment_id: UUID | None = None


@dataclass(frozen=True)
class FitProfile:
    """Constructed fit profile — the matching engine's input."""

    category_id: str
    maturity: ProfileMaturity
    sample_size: int
    dimensions: Mapping[str, DimensionStat]
    references: tuple[ReferenceGarment, ...]
    # Per-use-case overlays (PRD §6.2). Keyed by use case, then dimension.
    # Only use cases that some signal actually tags appear here; the matching
    # engine falls back to ``dimensions`` for anything else.
    use_case_variants: Mapping[str, Mapping[str, DimensionStat]] = field(default_factory=dict)

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
            dimensions=_as_preferences(self.dimensions),
            use_case_variants={
                use_case: _as_preferences(stats)
                for use_case, stats in self.use_case_variants.items()
            },
            hint=hint,
        )


# --- Construction ------------------------------------------------------------


def _as_preferences(stats: Mapping[str, DimensionStat]) -> dict[str, DimensionPreference]:
    return {
        name: DimensionPreference(
            preferred_cm=stat.preferred_cm,
            spread_cm=stat.spread_cm,
            sample_size=stat.sample_size,
        )
        for name, stat in stats.items()
    }


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


def _signal_for(
    garment: GarmentSnapshot, dimension: str, use_case: str | None
) -> SignalSnapshot | None:
    """The signal on ``dimension`` that applies under ``use_case``.

    For the unconditioned profile (``use_case is None``) only untagged
    signals count — a verdict the user gave specifically about the gym should
    not silently become their default preference.

    For a variant, a signal tagged with that use case wins, and an untagged
    signal is the fallback. That fallback is what makes a variant a genuine
    overlay: PRD §6.2's example garment is "preferred for casual, slightly
    short for layering", and under *layering* every other dimension should
    still carry its ordinary verdict rather than reverting to no feedback.
    """
    fallback: SignalSnapshot | None = None
    for sig in garment.signals:
        if sig.dimension != dimension:
            continue
        if use_case is not None and sig.use_case == use_case:
            return sig
        if sig.use_case is None:
            fallback = sig
    return fallback


def _dimension_stats(
    garments: Sequence[GarmentSnapshot],
    stated_fit_preference: StatedFitPreference | None,
    use_case: str | None,
) -> dict[str, DimensionStat]:
    """Posterior per dimension: weak prior updated by garment evidence."""
    evidence: dict[str, list[tuple[float, float]]] = {}
    for garment in garments:
        weight = RATING_WEIGHTS[garment.overall_rating]
        for dim, measured in garment.measurements_cm.items():
            effective = effective_measurement(measured, garment.stretch_level)
            sig = _signal_for(garment, dim, use_case)
            implied = effective + (_signal_shift_cm(sig) if sig else 0.0)
            evidence.setdefault(dim, []).append((weight, implied))

    priors = PRIOR_MEASUREMENTS_CM.get(stated_fit_preference) if stated_fit_preference else None

    stats: dict[str, DimensionStat] = {}
    for dim, points in evidence.items():
        # ``sample_size`` counts garments, never the prior — it is what the
        # UI shows as "based on N shirts" and what maturity is judged on.
        sample_size = len(points)

        weighted = list(points)
        prior_cm = priors.get(dim) if priors else None
        if prior_cm is not None:
            weighted.append((PRIOR_WEIGHT, prior_cm))

        total_w = sum(w for w, _ in weighted)
        if total_w <= 0:
            # Unreachable while every RATING_WEIGHTS value is positive (the
            # invariant ``test_every_rating_weight_is_positive`` pins). Kept
            # because the alternative to skipping the dimension is a
            # ZeroDivisionError on the next line, and a zero weight would
            # mean somebody deliberately zeroed out a rating — at which point
            # having no preference for that dimension is the right answer.
            continue
        preferred = sum(w * v for w, v in weighted) / total_w
        # Dispersion of the evidence (prior included) around the posterior
        # mean: a closet that disagrees with the stated preference is
        # genuinely less certain, and should say so.
        var = sum(w * (v - preferred) ** 2 for w, v in weighted) / total_w
        spread = max(MIN_SPREAD_CM, math.sqrt(var), BASE_SPREAD_CM / math.sqrt(sample_size))
        stats[dim] = DimensionStat(
            preferred_cm=round(preferred, 2),
            spread_cm=round(spread, 2),
            sample_size=sample_size,
        )
    return stats


def _tagged_use_cases(garments: Sequence[GarmentSnapshot]) -> list[str]:
    """Use cases some signal actually conditions on, in first-seen order.

    Driven by signal tags rather than the garments' own ``use_cases`` list:
    tagging a shirt "work" says where it is worn, while tagging a *signal*
    "work" says the fit verdict differs there. Only the latter creates a
    context-dependent preference (PRD §6.2), and building a variant from the
    former would just clone the default profile under a new name.
    """
    seen: dict[str, None] = {}
    for garment in garments:
        for sig in garment.signals:
            if sig.use_case is not None:
                seen.setdefault(sig.use_case, None)
    return list(seen)


def build_fit_profile(snapshot: ClosetSnapshot) -> FitProfile:
    """Construct a ``FitProfile`` from a closet snapshot (pure function)."""
    garments = list(snapshot.garments)
    preference = snapshot.stated_fit_preference

    dimensions = _dimension_stats(garments, preference, use_case=None)
    use_case_variants = {
        use_case: _dimension_stats(garments, preference, use_case=use_case)
        for use_case in _tagged_use_cases(garments)
    }

    references = tuple(
        ReferenceGarment(
            label=g.label,
            brand=g.brand,
            size_label=g.size_label,
            overall_rating=g.overall_rating,
            measurements_cm=dict(g.measurements_cm),
            use_cases=tuple(g.use_cases),
            garment_id=g.garment_id,
        )
        for g in garments
        if g.overall_rating is not OverallRating.DISLIKE
    )

    return FitProfile(
        category_id=snapshot.category_id,
        maturity=_maturity_for(len(garments)),
        sample_size=len(garments),
        dimensions=dimensions,
        references=references,
        use_case_variants=use_case_variants,
    )
