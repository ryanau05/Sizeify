"""Matching engine (PRD §6.4) — TKT-P1-14.

Pure ranking of a brand's available sizes against a ``FitProfile``. For each
size we compute, per dimension, the signed gap between the size chart's
(stretch-adjusted) measurement and the user's preferred value, then a
weighted distance using the canonical per-dimension weights.

A size whose every dimension lands within the profile's ``spread`` (its
preferred range) scores a distance of 0 — "sizes within range on all
dimensions score highest" (PRD §6.4). Among such ties, the residual weighted
absolute gap breaks the tie so the most centred size still wins.

Nothing to rank on
------------------
A cold-start profile has no dimensions, so there is no evidence to score any
size against. Every size then ties at distance 0, and ``within_range`` is
reported as ``False`` rather than vacuously ``True``: "every dimension is
inside the preferred range" must not be satisfiable by having no dimensions.
The caller decides what to do about it — ``domain.recommendation`` clamps
confidence for a cold-start profile, and PRD §13 asks the app to say "add a
few shirts to your closet first" instead of showing a size at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from api.domain.dimension_weights import BUTTON_DOWN_DIMENSION_WEIGHTS
from api.domain.fit_profile import FitProfile
from api.domain.stretch import effective_measurement
from api.schemas.enums import StretchLevel


class MissingDimensionError(KeyError):
    """A size chart lacks a dimension the fit profile needs.

    Typed (rather than a bare ``KeyError`` escaping from a dict lookup) so the
    caller can map it to a clear "this product's size chart is incomplete"
    response instead of a 500.
    """


@dataclass(frozen=True)
class BrandProduct:
    """A scraped product the engine ranks sizes for.

    ``size_chart`` maps a size label to canonical-dimension → cm measurements.
    ``stretch_level`` is the candidate fabric's stretch (defaults to none when
    the scraper can't determine it — the conservative choice).
    """

    brand: str
    product_name: str
    size_chart: Mapping[str, Mapping[str, float]]
    stretch_level: StretchLevel | None = None


@dataclass(frozen=True)
class RankedSize:
    """One size's fit against the profile."""

    size_label: str
    distance: float  # weighted out-of-range distance (0 = ideal)
    # True only when at least one dimension was scored AND every scored
    # dimension landed inside the profile's spread. False for a profile with
    # nothing to score — see "Nothing to rank on" in the module docstring.
    within_range: bool
    per_dimension_deltas: Mapping[str, float]  # signed (candidate_effective − preferred), cm
    residual: float  # weighted |delta| tiebreaker


def _weights_for(fit_profile: FitProfile) -> Mapping[str, float]:
    # v1 is button-downs only; the weights table is the single source of truth.
    return BUTTON_DOWN_DIMENSION_WEIGHTS


def match(
    fit_profile: FitProfile,
    brand_product: BrandProduct,
    *,
    use_case: str | None = None,
) -> list[RankedSize]:
    """Rank every size in ``brand_product``, best (smallest distance) first.

    Only dimensions present in BOTH the profile and the weights table are
    scored. If such a dimension is missing from a size's chart, raises
    ``MissingDimensionError``.

    ``use_case`` selects a per-use-case variant of the profile (PRD §6.4: "If
    the user's recent activity or the source URL provides hints about intended
    use case … the recommendation conditions on that use case"). A use case
    with no variant falls back to the unconditioned profile — see
    ``FitProfile.dimensions_for``.
    """
    weights = _weights_for(fit_profile)
    dimensions = fit_profile.dimensions_for(use_case)
    scored_dims = [d for d in dimensions if d in weights]

    # (sort key, size) pairs: the ordinal is a tiebreak, not part of the
    # result, so it does not belong on ``RankedSize``.
    scored: list[tuple[tuple[float, float, float, str], RankedSize]] = []
    for size_label, chart in brand_product.size_chart.items():
        distance = 0.0
        residual = 0.0
        # Vacuously-true would claim a fit we have no evidence for.
        within = bool(scored_dims)
        deltas: dict[str, float] = {}
        for dim in scored_dims:
            if dim not in chart:
                raise MissingDimensionError(
                    f"{brand_product.brand} size {size_label!r} chart is missing dimension {dim!r}"
                )
            stat = dimensions[dim]
            candidate = effective_measurement(chart[dim], brand_product.stretch_level)
            delta = candidate - stat.preferred_cm
            deltas[dim] = round(delta, 2)
            w = weights[dim]
            out_of_range = max(0.0, abs(delta) - stat.spread_cm)
            if out_of_range > 0:
                within = False
            distance += w * out_of_range
            residual += w * abs(delta)
        size = RankedSize(
            size_label=size_label,
            distance=round(distance, 4),
            within_range=within,
            per_dimension_deltas=deltas,
            residual=round(residual, 4),
        )
        # Sum of the size's own scored measurements: a stable stand-in for
        # "how big is this garment", used only to break exact ties.
        ordinal = sum(chart[dim] for dim in scored_dims)
        scored.append(((size.distance, size.residual, ordinal, size_label), size))

    # Ties used to fall out of ``size_chart`` dict iteration order. Once charts
    # load from the ``brand_product`` JSONB column that is whatever Postgres
    # chose (jsonb sorts keys by length then bytewise and does not preserve
    # input order), so the recommended size could change between a fresh scrape
    # and a round-tripped one. Rounding distance and residual to 4 places makes
    # exact ties likelier than raw floats would, which makes this matter more.
    #
    # When two sizes tie on both metrics the model genuinely has no preference,
    # so the tiebreak only has to be reproducible. It breaks on the smaller
    # garment first, which at least reads as a rule; the label is a final
    # fallback for two sizes with identical measurements under different names.
    # Sorting on the label alone would have been reproducible but nonsense —
    # "L" sorts before "M" before "S".
    scored.sort(key=lambda pair: pair[0])
    return [size for _, size in scored]
