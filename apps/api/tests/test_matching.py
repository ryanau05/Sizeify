"""Unit tests for the matching engine (TKT-P1-14) — PRD §6.4, §6.3.

The ticket's four acceptance cases: an in-range fit picks the closest size,
weighted distance respects the per-dimension weights, stretch adjustment
changes the ranking, and a chart missing a scored dimension raises a typed
error.

Most fixtures use ``spread=0`` so every gap is out of range and the distance
is driven purely by the weights — with a realistic spread the interesting
sizes all tie at distance 0 and the weighting is invisible. Tests that care
about the in-range path say so and set a real spread.
"""

from __future__ import annotations

import pytest

from api.domain.dimension_weights import BUTTON_DOWN_DIMENSION_WEIGHTS
from api.domain.fit_profile import DimensionStat, FitProfile
from api.domain.matching import BrandProduct, MissingDimensionError, RankedSize, match
from api.schemas.enums import ProfileMaturity, StretchLevel

CATEGORY = "mens_button_down_shirt"


def profile(
    *, spread: float = 0.0, maturity: ProfileMaturity | None = None, **dims: float
) -> FitProfile:
    return FitProfile(
        category_id=CATEGORY,
        maturity=maturity or ProfileMaturity.DEVELOPING,
        sample_size=5,
        dimensions={name: DimensionStat(value, spread, 5) for name, value in dims.items()},
        references=(),
    )


def labels(ranked: list[RankedSize]) -> list[str]:
    return [size.size_label for size in ranked]


# ---------------------------------------------------------------------------
# In-range fit picks the closest size.
# ---------------------------------------------------------------------------


def test_in_range_size_ranks_first() -> None:
    ranked = match(
        profile(spread=2.0, chest=107.0, shoulder_width=46.0),
        BrandProduct(
            brand="jcrew",
            product_name="Bowery",
            size_chart={
                "S": {"chest": 102.0, "shoulder_width": 44.0},
                "M": {"chest": 107.0, "shoulder_width": 46.0},
                "L": {"chest": 112.0, "shoulder_width": 48.0},
            },
        ),
    )

    assert labels(ranked) == ["M", "S", "L"]
    assert ranked[0].distance == 0.0
    assert ranked[0].within_range is True
    assert ranked[0].per_dimension_deltas == {"chest": 0.0, "shoulder_width": 0.0}


def test_sizes_within_spread_tie_at_zero_and_the_most_centred_wins() -> None:
    """PRD §6.4: "sizes within range on all dimensions score highest". Both
    candidates sit inside a 3 cm spread, so both score 0 — the residual
    weighted gap is what separates them.

      centred: |54.1 - 54.0| = 0.1 → residual 0.35 * 0.1 = 0.035
      edge:    |56.9 - 54.0| = 2.9 → residual 0.35 * 2.9 = 1.015
    """
    ranked = match(
        profile(spread=3.0, chest=54.0),
        BrandProduct("b", "p", {"edge": {"chest": 56.9}, "centred": {"chest": 54.1}}),
    )

    assert labels(ranked) == ["centred", "edge"]
    assert [size.distance for size in ranked] == [0.0, 0.0]
    assert [size.within_range for size in ranked] == [True, True]
    assert ranked[0].residual == 0.035
    assert ranked[1].residual == 1.015


def test_out_of_range_size_is_not_marked_within_range() -> None:
    ranked = match(
        profile(spread=1.0, chest=54.0),
        BrandProduct("b", "p", {"M": {"chest": 60.0}}),
    )

    assert ranked[0].within_range is False
    # Distance counts only the excess beyond the spread: 6.0 - 1.0 = 5.0.
    assert ranked[0].distance == pytest.approx(0.35 * 5.0)
    assert ranked[0].per_dimension_deltas == {"chest": 6.0}


# ---------------------------------------------------------------------------
# Weighted distance respects the per-dimension weights.
# ---------------------------------------------------------------------------


def test_ranking_follows_the_dimension_weights_not_raw_error() -> None:
    """The acceptance case, isolated: two sizes with the *same* total
    absolute error, differing only in which dimension carries it.

      chest-off: 2.0 cm on chest              → 0.35 * 2.0 = 0.70
      cuff-off:  2.0 cm on cuff_circumference → 0.05 * 2.0 = 0.10

    Chest is weighted seven times more heavily than cuff (PRD §6.4), so the
    size that errs on the cuff wins. With equal weights this test would fail,
    which is what makes it a test of the weighting rather than of ordering.
    """
    ranked = match(
        profile(chest=54.0, cuff_circumference=23.0),
        BrandProduct(
            "b",
            "p",
            {
                "chest-off": {"chest": 56.0, "cuff_circumference": 23.0},
                "cuff-off": {"chest": 54.0, "cuff_circumference": 25.0},
            },
        ),
    )

    assert labels(ranked) == ["cuff-off", "chest-off"]
    assert ranked[0].distance == pytest.approx(0.10)
    assert ranked[1].distance == pytest.approx(0.70)


def test_distance_is_the_weighted_sum_across_dimensions() -> None:
    """0.35 * 2.0 (chest) + 0.20 * 1.0 (shoulder) = 0.90."""
    ranked = match(
        profile(chest=54.0, shoulder_width=46.0),
        BrandProduct("b", "p", {"M": {"chest": 56.0, "shoulder_width": 47.0}}),
    )

    assert ranked[0].distance == pytest.approx(0.35 * 2.0 + 0.20 * 1.0)


def test_dimensions_outside_the_weights_table_are_ignored() -> None:
    """The profile can only hold dimensions the category declares, and the
    weights table covers all of them — but a dimension with no weight must be
    skipped rather than scored at an implicit weight."""
    ranked = match(
        profile(chest=54.0, hem_width=40.0),
        BrandProduct("b", "p", {"M": {"chest": 56.0}}),  # no hem_width in the chart
    )

    assert ranked[0].per_dimension_deltas == {"chest": 2.0}
    assert ranked[0].distance == pytest.approx(0.35 * 2.0)


def test_weights_table_is_the_one_from_tkt_p1_02() -> None:
    """The engine must score with the canonical weights, not a private copy —
    CLAUDE.md's four-place rule depends on there being a single table."""
    ranked = match(
        profile(chest=54.0),
        BrandProduct("b", "p", {"M": {"chest": 55.0}}),
    )

    assert ranked[0].distance == pytest.approx(BUTTON_DOWN_DIMENSION_WEIGHTS["chest"] * 1.0)


# ---------------------------------------------------------------------------
# Stretch adjustment (PRD §6.3).
# ---------------------------------------------------------------------------


def test_stretch_changes_which_size_wins() -> None:
    """The acceptance case: the same chart ranks differently once the fabric's
    stretch is accounted for. Preferred chest 110.0:

      no stretch  → S |104.0 - 110| = 6.0 ; M |111.0 - 110| = 1.0  → M
      high (+3.5) → S |107.5 - 110| = 2.5 ; M |114.5 - 110| = 4.5  → S

    Stretch only ever adds room, so it can only ever favour the smaller size —
    which is exactly the failure mode it exists to prevent (recommending a
    size up in a fabric that already gives).
    """
    chart = {"S": {"chest": 104.0}, "M": {"chest": 111.0}}
    prof = profile(chest=110.0)

    assert labels(match(prof, BrandProduct("b", "p", chart, StretchLevel.NONE))) == ["M", "S"]
    assert labels(match(prof, BrandProduct("b", "p", chart, StretchLevel.HIGH))) == ["S", "M"]


def test_stretch_shifts_every_delta_by_the_level_offset() -> None:
    chart = {"M": {"chest": 107.0}}
    prof = profile(chest=110.0)

    none = match(prof, BrandProduct("b", "p", chart, StretchLevel.NONE))[0]
    moderate = match(prof, BrandProduct("b", "p", chart, StretchLevel.MODERATE))[0]

    assert none.per_dimension_deltas["chest"] == -3.0
    # Moderate stretch is +2.0 cm (PRD §6.3 anchor).
    assert moderate.per_dimension_deltas["chest"] == -1.0


def test_unknown_stretch_level_is_treated_as_none() -> None:
    """A scraper that cannot determine the fabric must not make the engine
    assume stretch the garment may not have."""
    chart = {"M": {"chest": 107.0}}
    prof = profile(chest=110.0)

    unknown = match(prof, BrandProduct("b", "p", chart, None))[0]
    explicit_none = match(prof, BrandProduct("b", "p", chart, StretchLevel.NONE))[0]

    assert unknown == explicit_none


# ---------------------------------------------------------------------------
# Missing dimension in the product chart.
# ---------------------------------------------------------------------------


def test_missing_dimension_raises_typed_error() -> None:
    with pytest.raises(MissingDimensionError) as exc:
        match(
            profile(chest=107.0, shoulder_width=46.0),
            BrandProduct("jcrew", "p", {"M": {"chest": 107.0}}),
        )

    # The message has to be enough to chase down a broken scraper fixture.
    message = str(exc.value)
    assert "shoulder_width" in message
    assert "jcrew" in message
    assert "'M'" in message


def test_missing_dimension_error_is_a_key_error() -> None:
    """Subclassing ``KeyError`` keeps ``except KeyError`` call sites working
    while letting the recommendation path tell "incomplete size chart" apart
    from any other lookup failure."""
    assert issubclass(MissingDimensionError, KeyError)


def test_missing_dimension_on_a_later_size_still_raises() -> None:
    """A chart where only some sizes are complete is still a broken chart —
    ranking the good ones and silently dropping the rest would hide it."""
    with pytest.raises(MissingDimensionError):
        match(
            profile(chest=54.0, shoulder_width=46.0),
            BrandProduct(
                "b",
                "p",
                {
                    "S": {"chest": 52.0, "shoulder_width": 44.0},
                    "M": {"chest": 54.0},  # incomplete
                },
            ),
        )


# ---------------------------------------------------------------------------
# Degenerate inputs.
# ---------------------------------------------------------------------------


def test_cold_start_profile_never_claims_a_size_is_within_range() -> None:
    """A profile with no dimensions gives the engine nothing to score.

    Every size ties at distance 0, and ``within_range`` must be False —
    "every dimension is inside the preferred range" cannot be earned by
    having no dimensions. Reporting True here would hand a brand-new user a
    confident-looking recommendation for whichever size the scraper happened
    to list first.
    """
    ranked = match(
        profile(maturity=ProfileMaturity.COLD_START),
        BrandProduct("b", "p", {"XL": {"chest": 120.0}, "S": {"chest": 96.0}}),
    )

    assert [size.within_range for size in ranked] == [False, False]
    assert [size.distance for size in ranked] == [0.0, 0.0]
    assert all(size.per_dimension_deltas == {} for size in ranked)


def test_empty_size_chart_ranks_nothing() -> None:
    assert match(profile(chest=54.0), BrandProduct("b", "p", {})) == []


def test_ranking_is_deterministic() -> None:
    """Two calls on the same inputs must agree — the share-sheet flow can
    retry, and a shuffling recommendation would be worse than a wrong one."""
    prof = profile(spread=2.0, chest=54.0, shoulder_width=46.0)
    product = BrandProduct(
        "b",
        "p",
        {
            "S": {"chest": 52.0, "shoulder_width": 44.0},
            "M": {"chest": 54.0, "shoulder_width": 46.0},
            "L": {"chest": 56.0, "shoulder_width": 48.0},
        },
    )

    assert match(prof, product) == match(prof, product)
