"""Recommendation assembly (PRD §5.4 / §6.4) — TKT-P1-15.

Wraps the matching engine with the confidence rule and assembles the four
mandatory PRD §5.4 components: a recommended **size**, a **confidence** score,
human-readable **fit notes**, and the **reference garments** that drove the
call. When confidence is below 60% a second candidate is returned with a
trade-off string (PRD §5.4); a cold-start profile clamps confidence to ≤ 0.50.

The output shape is the API contract for the recommendation response; the
Pydantic response model in the route layer mirrors it field for field.

Refusing rather than guessing
----------------------------
PRD §5.4 makes all four components mandatory, so a profile with nothing to
score cannot produce a valid recommendation: there are no per-dimension deltas
to narrate, and every size ties at distance 0 so the "best" one would be
whichever the scraper happened to list first. ``recommend`` raises
``InsufficientClosetDataError`` in that case instead, which is the branch PRD
§13 describes — "Add a few shirts to your closet first to get
recommendations." A low-confidence answer is useful; an arbitrary one dressed
up as a recommendation is not.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from api.domain.fit_profile import FitProfile
from api.domain.fit_profile import ReferenceGarment as _RefGarment
from api.domain.matching import BrandProduct, RankedSize, match
from api.schemas.enums import OverallRating, ProfileMaturity


class InsufficientClosetDataError(ValueError):
    """The fit profile has no scored dimension to rank sizes against.

    Raised instead of returning a recommendation whose fit notes would be
    empty (PRD §5.4 requires them) and whose size would be an artifact of size
    chart ordering. The caller maps this to PRD §13's "add a few shirts first"
    notification rather than to an error the user sees as a failure.
    """


# Confidence < this → show two candidates with a trade-off (PRD §5.4).
CONFIDENCE_TWO_CANDIDATE_THRESHOLD = 0.60
# A cold-start profile can never be presented as confident (PRD §6.2).
COLD_START_CONFIDENCE_CAP = 0.50

# Confidence weighting: closeness of best size, gap to runner-up, profile certainty.
_W_CLOSENESS = 0.45
_W_GAP = 0.30
_W_CERTAINTY = 0.25

_CERTAINTY_BY_MATURITY: Mapping[ProfileMaturity, float] = {
    ProfileMaturity.COLD_START: 0.40,
    ProfileMaturity.DEVELOPING: 0.80,
    ProfileMaturity.MATURE: 1.0,
}

# Slight ranking boost so garments the user loves/likes surface as references first.
_RATING_BONUS: Mapping[OverallRating, float] = {
    OverallRating.LOVE: -2.0,
    OverallRating.LIKE: -1.0,
}

# Human-readable dimension labels for fit notes.
_DIM_LABEL: Mapping[str, str] = {
    "chest": "Chest",
    "shoulder_width": "Shoulders",
    "body_length": "Body length",
    "sleeve_length": "Sleeve length",
    "neck_circumference": "Neck",
    "cuff_circumference": "Cuff",
}


@dataclass(frozen=True)
class ReferenceGarmentOut:
    label: str
    brand: str
    size_label: str
    why: str
    # ``owned_garment.id`` when the profile was built from database rows.
    # ``recommendation.reference_garment_ids`` is populated from these
    # (TKT-P1-16); ``None`` for a hand-built profile in a unit test.
    garment_id: UUID | None = None

    def to_wire(self) -> dict[str, str]:
        return {
            "label": self.label,
            "brand": self.brand,
            "size_label": self.size_label,
            "why": self.why,
        }


@dataclass(frozen=True)
class SizeCandidate:
    size_label: str
    fit_notes: Sequence[str]
    tradeoff: str | None = None

    def to_wire(self) -> dict[str, object]:
        out: dict[str, object] = {
            "size_label": self.size_label,
            "fit_notes": list(self.fit_notes),
        }
        if self.tradeoff is not None:
            out["tradeoff"] = self.tradeoff
        return out


@dataclass(frozen=True)
class Recommendation:
    brand: str
    product_name: str
    primary: SizeCandidate
    confidence: float
    reference_garments: Sequence[ReferenceGarmentOut] = field(default_factory=tuple)
    alternate: SizeCandidate | None = None

    def to_wire(self) -> dict[str, object]:
        out: dict[str, object] = {
            "brand": self.brand,
            "product_name": self.product_name,
            "primary": self.primary.to_wire(),
            "confidence": self.confidence,
            "reference_garments": [r.to_wire() for r in self.reference_garments],
        }
        if self.alternate is not None:
            out["alternate"] = self.alternate.to_wire()
        return out


def _fit_note(dim: str, delta: float, spread: float) -> str:
    label = _DIM_LABEL.get(dim, dim.replace("_", " ").title())
    if abs(delta) <= spread:
        return f"{label}: right in your preferred range."
    if delta > 0:
        return f"{label}: about {abs(delta):.0f} cm roomier than you prefer."
    return f"{label}: about {abs(delta):.0f} cm slimmer/shorter than you prefer."


def _fit_notes_for(fit_profile: FitProfile, size: RankedSize) -> list[str]:
    notes: list[str] = []
    for dim, delta in size.per_dimension_deltas.items():
        spread = fit_profile.dimensions[dim].spread_cm
        notes.append(_fit_note(dim, delta, spread))
    return notes


def _relevance(ref: _RefGarment, size: RankedSize, fit_profile: FitProfile) -> float:
    """Lower = more similar to the recommended size. Absolute cm gap on shared
    dimensions.

    PRD §6.4 asks for the reasoning to compare "the recommended size's
    measurements directly to the user's most relevant owned garments", so the
    comparison is against the candidate, not against the profile's preferred
    value. The two coincide when the size fits well and diverge when it does
    not — and it is exactly the poor-fit case where the user needs the
    reference garment to be the one the recommendation actually resembles.

    ``per_dimension_deltas`` holds ``candidate_effective − preferred``, so the
    candidate's own measurement is recovered by adding the delta back.
    """
    total = 0.0
    for dim, delta in size.per_dimension_deltas.items():
        if dim in ref.measurements_cm:
            candidate_cm = fit_profile.dimensions[dim].preferred_cm + delta
            total += abs(ref.measurements_cm[dim] - candidate_cm)
    # Slight boost for love/like garments so they surface first when relevant.
    rating_bonus = (
        _RATING_BONUS.get(ref.overall_rating, 0.0) if ref.overall_rating is not None else 0.0
    )
    return total + rating_bonus


def _reference_garments_for(
    fit_profile: FitProfile, size: RankedSize, limit: int = 2
) -> list[ReferenceGarmentOut]:
    refs = sorted(fit_profile.references, key=lambda r: _relevance(r, size, fit_profile))
    out: list[ReferenceGarmentOut] = []
    for ref in refs[:limit]:
        rated = (
            f"you rated {ref.overall_rating.value}"
            if ref.overall_rating is not None
            else "in your closet"
        )
        out.append(
            ReferenceGarmentOut(
                label=ref.label,
                brand=ref.brand,
                size_label=ref.size_label,
                why=f"Your {ref.brand} {ref.size_label} ({rated}) sits closest to this fit.",
                garment_id=ref.garment_id,
            )
        )
    return out


def _confidence(fit_profile: FitProfile, best: RankedSize, second: RankedSize | None) -> float:
    closeness = 1.0 / (1.0 + 0.6 * best.distance)  # 1.0 when best is fully in range
    if second is None:
        gap_score = 0.55  # only one size offered — moderate, can't compare
    else:
        gap = max(0.0, second.distance - best.distance)
        gap_score = 1.0 - math.exp(-gap / 2.0)
    certainty = _CERTAINTY_BY_MATURITY[fit_profile.maturity]
    base = _W_CLOSENESS * closeness + _W_GAP * gap_score + _W_CERTAINTY * certainty
    if fit_profile.maturity is ProfileMaturity.COLD_START:
        base = min(base, COLD_START_CONFIDENCE_CAP)
    return round(max(0.0, min(1.0, base)), 2)


def recommend(fit_profile: FitProfile, brand_product: BrandProduct) -> Recommendation:
    """Produce a size recommendation with all four PRD §5.4 components."""
    ranked = match(fit_profile, brand_product)
    if not ranked:
        raise ValueError("brand_product has no sizes to rank")

    best = ranked[0]
    if not best.per_dimension_deltas:
        # Nothing was scored — see "Refusing rather than guessing" above.
        raise InsufficientClosetDataError(
            "fit profile has no scored dimensions; cannot recommend a size for "
            f"{brand_product.brand} {brand_product.product_name!r}"
        )
    if not fit_profile.references:
        # PRD §5.4 makes reference garments mandatory, and the profile has
        # none to cite. Reachable with a closet the user rated entirely
        # "dislike": those are excluded from ``references`` (correctly —
        # citing a shirt they hate as the reason for a size is worse than
        # citing nothing), which leaves the recommendation two components
        # short of the four it promises. Refuse for the same reason as
        # above rather than ship a partial one.
        raise InsufficientClosetDataError(
            "fit profile has no citable reference garments (every garment is rated "
            f"'dislike'); cannot recommend a size for {brand_product.brand} "
            f"{brand_product.product_name!r}"
        )

    second = ranked[1] if len(ranked) > 1 else None
    confidence = _confidence(fit_profile, best, second)

    primary = SizeCandidate(
        size_label=best.size_label,
        fit_notes=_fit_notes_for(fit_profile, best),
    )
    references = _reference_garments_for(fit_profile, best)

    alternate: SizeCandidate | None = None
    if confidence < CONFIDENCE_TWO_CANDIDATE_THRESHOLD and second is not None:
        # Orient the trade-off by where the runner-up sits overall. Summing
        # signed deltas across dimensions is crude — it treats a cm of neck
        # like a cm of chest — but it only picks which of two words to print,
        # and the sizes it separates differ in the same direction on nearly
        # every dimension anyway.
        roomier = sum(second.per_dimension_deltas.values()) > sum(
            best.per_dimension_deltas.values()
        )
        direction = "roomier" if roomier else "slimmer"
        primary = SizeCandidate(
            size_label=best.size_label,
            fit_notes=primary.fit_notes,
            tradeoff=f"Closest overall fit; {best.size_label} vs {second.size_label} is a "
            f"toss-up given your closet.",
        )
        alternate = SizeCandidate(
            size_label=second.size_label,
            fit_notes=_fit_notes_for(fit_profile, second),
            tradeoff=f"A {direction} alternative if you prefer that over the {best.size_label}.",
        )

    return Recommendation(
        brand=brand_product.brand,
        product_name=brand_product.product_name,
        primary=primary,
        confidence=confidence,
        reference_garments=references,
        alternate=alternate,
    )
