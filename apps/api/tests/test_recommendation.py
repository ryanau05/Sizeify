"""Unit tests for recommendation assembly (TKT-P1-15)."""

from __future__ import annotations

from api.domain.fit_profile import DimensionStat, FitProfile, ReferenceGarment
from api.domain.matching import BrandProduct
from api.domain.recommendation import (
    CONFIDENCE_TWO_CANDIDATE_THRESHOLD,
    recommend,
)
from api.schemas.enums import OverallRating, ProfileMaturity


def _profile(maturity=ProfileMaturity.DEVELOPING, sample_size=5):
    return FitProfile(
        category_id="mens_button_down_shirt",
        maturity=maturity,
        sample_size=sample_size,
        dimensions={
            "chest": DimensionStat(107.0, 2.0, sample_size),
            "shoulder_width": DimensionStat(46.0, 2.0, sample_size),
        },
        references=(
            ReferenceGarment(
                "Uniqlo M",
                "uniqlo",
                "M",
                OverallRating.LOVE,
                {"chest": 105.0, "shoulder_width": 45.0},
                (),
            ),
        ),
    )


_PRODUCT = BrandProduct(
    brand="jcrew",
    product_name="Bowery Wrinkle-Free Dress Shirt",
    size_chart={
        "S": {"chest": 102.0, "shoulder_width": 44.0},
        "M": {"chest": 107.0, "shoulder_width": 46.0},
        "L": {"chest": 112.0, "shoulder_width": 48.0},
        "XL": {"chest": 117.0, "shoulder_width": 50.0},
    },
)


def test_confident_closet_returns_single_candidate_with_four_components():
    rec = recommend(_profile(), _PRODUCT)
    # (1) size, (2) confidence, (3) fit notes, (4) reference garments
    assert rec.primary.size_label == "M"
    assert 0.0 <= rec.confidence <= 1.0
    assert rec.confidence >= CONFIDENCE_TWO_CANDIDATE_THRESHOLD
    assert rec.primary.fit_notes  # non-empty
    assert rec.reference_garments
    assert rec.alternate is None


def test_cold_start_is_capped_and_returns_two_candidates():
    rec = recommend(_profile(maturity=ProfileMaturity.COLD_START, sample_size=2), _PRODUCT)
    assert rec.confidence <= 0.50  # cold-start cap (PRD §6.2)
    assert rec.confidence < CONFIDENCE_TWO_CANDIDATE_THRESHOLD
    assert rec.alternate is not None
    assert rec.alternate.tradeoff  # trade-off string present (PRD §5.4)


def test_wire_shape_matches_types_ts():
    wire = recommend(_profile(), _PRODUCT).to_wire()
    assert set(wire) >= {"brand", "product_name", "primary", "confidence", "reference_garments"}
    assert set(wire["primary"]) >= {"size_label", "fit_notes"}
    for ref in wire["reference_garments"]:
        assert set(ref) == {"label", "brand", "size_label", "why"}
