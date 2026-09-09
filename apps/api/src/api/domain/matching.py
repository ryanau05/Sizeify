"""Matching engine (PRD §6.4) — TKT-P1-14.

Pure ranking of a brand's available sizes against a ``FitProfile``. For each
size we compute, per dimension, the signed gap between the size chart's
(stretch-adjusted) measurement and the user's preferred value, then a
weighted distance using the canonical per-dimension weights.

A size whose every dimension lands within the profile's ``spread`` (its
preferred range) scores a distance of 0 — "sizes within range on all
dimensions score highest" (PRD §6.4). Among such ties, the residual weighted
absolute gap breaks the tie so the most centred size still wins.
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
    within_range: bool  # every weighted dimension inside spread
    per_dimension_deltas: Mapping[str, float]  # signed (candidate_effective − preferred), cm
    residual: float  # weighted |delta| tiebreaker


def _weights_for(fit_profile: FitProfile) -> Mapping[str, float]:
    # v1 is button-downs only; the weights table is the single source of truth.
    return BUTTON_DOWN_DIMENSION_WEIGHTS


def match(fit_profile: FitProfile, brand_product: BrandProduct) -> list[RankedSize]:
    """Rank every size in ``brand_product``, best (smallest distance) first.

    Only dimensions present in BOTH the profile and the weights table are
    scored. If such a dimension is missing from a size's chart, raises
    ``MissingDimensionError``.
    """
    weights = _weights_for(fit_profile)
    scored_dims = [d for d in fit_profile.dimensions if d in weights]

    ranked: list[RankedSize] = []
    for size_label, chart in brand_product.size_chart.items():
        distance = 0.0
        residual = 0.0
        within = True
        deltas: dict[str, float] = {}
        for dim in scored_dims:
            if dim not in chart:
                raise MissingDimensionError(
                    f"{brand_product.brand} size {size_label!r} chart is missing dimension {dim!r}"
                )
            stat = fit_profile.dimensions[dim]
            candidate = effective_measurement(chart[dim], brand_product.stretch_level)
            delta = candidate - stat.preferred_cm
            deltas[dim] = round(delta, 2)
            w = weights[dim]
            out_of_range = max(0.0, abs(delta) - stat.spread_cm)
            if out_of_range > 0:
                within = False
            distance += w * out_of_range
            residual += w * abs(delta)
        ranked.append(
            RankedSize(
                size_label=size_label,
                distance=round(distance, 4),
                within_range=within,
                per_dimension_deltas=deltas,
                residual=round(residual, 4),
            )
        )

    ranked.sort(key=lambda r: (r.distance, r.residual))
    return ranked
