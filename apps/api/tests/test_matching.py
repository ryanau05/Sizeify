"""Unit tests for the matching engine (DEMO-03 / TKT-P1-14)."""

from __future__ import annotations

import pytest

from api.domain.fit_profile import DimensionStat, FitProfile
from api.domain.matching import BrandProduct, MissingDimensionError, match
from api.schemas.enums import ProfileMaturity, StretchLevel


def _profile(**dims):
    return FitProfile(
        category_id="mens_button_down_shirt",
        maturity=ProfileMaturity.DEVELOPING,
        sample_size=5,
        dimensions={k: DimensionStat(v, 2.0, 5) for k, v in dims.items()},
        references=(),
    )


def test_in_range_size_ranks_first():
    profile = _profile(chest=107.0, shoulder_width=46.0)
    product = BrandProduct(
        brand="jcrew",
        product_name="Bowery",
        size_chart={
            "S": {"chest": 102.0, "shoulder_width": 44.0},
            "M": {"chest": 107.0, "shoulder_width": 46.0},
            "L": {"chest": 112.0, "shoulder_width": 48.0},
        },
    )
    ranked = match(profile, product)
    assert ranked[0].size_label == "M"
    assert ranked[0].within_range is True
    assert ranked[0].distance == 0.0


def test_weighted_distance_orders_remaining_sizes():
    profile = _profile(chest=107.0, shoulder_width=46.0)
    product = BrandProduct(
        brand="b",
        product_name="p",
        size_chart={
            "M": {"chest": 107.0, "shoulder_width": 46.0},
            "L": {"chest": 112.0, "shoulder_width": 48.0},
            "XL": {"chest": 118.0, "shoulder_width": 50.0},
        },
    )
    labels = [r.size_label for r in match(profile, product)]
    assert labels == ["M", "L", "XL"]


def test_missing_dimension_raises_typed_error():
    profile = _profile(chest=107.0, shoulder_width=46.0)
    product = BrandProduct(
        brand="b",
        product_name="p",
        size_chart={"M": {"chest": 107.0}},  # shoulder_width absent
    )
    with pytest.raises(MissingDimensionError):
        match(profile, product)


def test_stretch_changes_effective_deltas():
    profile = _profile(chest=110.0)
    chart = {"M": {"chest": 107.0}}
    no_stretch = match(profile, BrandProduct("b", "p", chart, StretchLevel.NONE))[0]
    high_stretch = match(profile, BrandProduct("b", "p", chart, StretchLevel.HIGH))[0]
    # High stretch makes the 107 cm chest wear ~3.5 cm larger → closer to the
    # preferred 110, i.e. a smaller (less negative) gap.
    assert high_stretch.per_dimension_deltas["chest"] > no_stretch.per_dimension_deltas["chest"]
