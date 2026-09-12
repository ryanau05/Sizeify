"""Unit tests for fit-profile construction (TKT-P1-12) — PRD §6.2.

The ticket names five fixtures: empty closet, a single love-rated garment,
contradictory signals, use-case-specific signals, and maturity. Each asserts
the *whole* profile shape — every dimension's preferred value and spread, plus
maturity and sample size — rather than a relative comparison, so a change in
the weighting model shows up here as a concrete number rather than passing
silently because an inequality still holds.

Expected values are hand-computed in the comment above each assertion. If one
of them starts failing, the arithmetic in the comment is the specification and
the code is what changed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields

import pytest

from api.domain.fit_profile import (
    BASE_SPREAD_CM,
    PRIOR_MEASUREMENTS_CM,
    PRIOR_WEIGHT,
    RATING_WEIGHTS,
    ClosetSnapshot,
    DimensionStat,
    GarmentSnapshot,
    ReferenceGarment,
    SignalSnapshot,
    build_fit_profile,
)
from api.schemas.enums import (
    OverallRating,
    ProfileMaturity,
    StatedFitPreference,
    StretchLevel,
    Verdict,
)
from api.seeds.garment_categories import _BUTTON_DOWN_MEASUREMENT_SCHEMA

CATEGORY = "mens_button_down_shirt"


def garment(
    label: str,
    *,
    measurements: dict[str, float] | None = None,
    chest: float | None = None,
    rating: OverallRating | None = None,
    stretch: StretchLevel | None = None,
    signals: Sequence[SignalSnapshot] = (),
    use_cases: Sequence[str] = (),
    brand: str = "Uniqlo",
    size_label: str = "M",
) -> GarmentSnapshot:
    if measurements is None:
        measurements = {"chest": chest if chest is not None else 54.0}
    return GarmentSnapshot(
        label=label,
        brand=brand,
        size_label=size_label,
        measurements_cm=measurements,
        stretch_level=stretch,
        overall_rating=rating,
        use_cases=tuple(use_cases),
        signals=tuple(signals),
    )


def closet(
    *garments: GarmentSnapshot,
    stated: StatedFitPreference | None = None,
) -> ClosetSnapshot:
    return ClosetSnapshot(CATEGORY, list(garments), stated)


# ---------------------------------------------------------------------------
# Fixture 1 — empty closet.
# ---------------------------------------------------------------------------


def test_empty_closet_is_cold_start_with_no_dimensions() -> None:
    profile = build_fit_profile(closet())

    assert profile.category_id == CATEGORY
    assert profile.maturity is ProfileMaturity.COLD_START
    assert profile.sample_size == 0
    assert profile.dimensions == {}
    assert profile.use_case_variants == {}
    assert profile.references == ()


def test_empty_closet_with_a_stated_preference_still_has_no_dimensions() -> None:
    """The prior never invents a dimension.

    ``FitProfileResponse`` documents an empty closet as cold start with both
    dimension maps empty, and TKT-P1-13 returns a hint on the strength of it.
    A user who has entered nothing must not be handed a fabricated
    six-dimension profile derived purely from one onboarding tap.
    """
    profile = build_fit_profile(closet(stated=StatedFitPreference.RELAXED))

    assert profile.dimensions == {}
    assert profile.maturity is ProfileMaturity.COLD_START


def test_empty_closet_response_carries_the_cold_start_hint() -> None:
    response = build_fit_profile(closet()).to_response()

    assert response.maturity is ProfileMaturity.COLD_START
    assert response.dimensions == {}
    assert response.use_case_variants == {}
    assert response.hint is not None


# ---------------------------------------------------------------------------
# Fixture 2 — a single love-rated garment.
# ---------------------------------------------------------------------------


def test_single_love_rated_garment_anchors_the_profile() -> None:
    """PRD §6.2: a loved garment with "preferred" verdicts anchors the range.

    One garment, weight 1.0 (love), no signal shift, no prior:
      preferred = 54.0
      dispersion = 0, so spread = BASE_SPREAD_CM / sqrt(1) = 3.0
    """
    profile = build_fit_profile(
        closet(
            garment(
                "Uniqlo Oxford",
                measurements={"chest": 54.0, "sleeve_length": 63.0},
                rating=OverallRating.LOVE,
                signals=[
                    SignalSnapshot("chest", Verdict.PREFERRED),
                    SignalSnapshot("sleeve_length", Verdict.PREFERRED),
                ],
            )
        )
    )

    assert profile.maturity is ProfileMaturity.COLD_START
    assert profile.sample_size == 1
    assert dict(profile.dimensions) == {
        "chest": DimensionStat(preferred_cm=54.0, spread_cm=3.0, sample_size=1),
        "sleeve_length": DimensionStat(preferred_cm=63.0, spread_cm=3.0, sample_size=1),
    }
    assert [ref.label for ref in profile.references] == ["Uniqlo Oxford"]


def test_love_rated_garment_marked_slightly_tight_shifts_the_preference_up() -> None:
    """PRD §6.2: "love" but one measurement "slightly tight" updates the
    preferred range to be *larger* than that measurement.

    54.0 measured + 1.5 (default slightly-tight shift) = 55.5.
    """
    profile = build_fit_profile(
        closet(
            garment(
                "a",
                chest=54.0,
                rating=OverallRating.LOVE,
                signals=[SignalSnapshot("chest", Verdict.SLIGHTLY_TIGHT)],
            )
        )
    )

    assert dict(profile.dimensions) == {
        "chest": DimensionStat(preferred_cm=55.5, spread_cm=3.0, sample_size=1)
    }


def test_explicit_magnitude_overrides_the_default_verdict_shift() -> None:
    """54.0 + 2.5 (stated magnitude, oriented by the verdict) = 56.5."""
    profile = build_fit_profile(
        closet(
            garment(
                "a",
                chest=54.0,
                rating=OverallRating.LOVE,
                signals=[SignalSnapshot("chest", Verdict.SLIGHTLY_TIGHT, magnitude_cm=2.5)],
            )
        )
    )

    assert profile.dimensions["chest"].preferred_cm == 56.5


# ---------------------------------------------------------------------------
# Fixture 3 — contradictory signals.
# ---------------------------------------------------------------------------


def test_contradictory_signals_widen_the_spread() -> None:
    """Two loved garments that disagree outright about the chest.

      A: 50.0 measured, "too loose"  → 50.0 - 4.0 = 46.0
      B: 58.0 measured, "too tight"  → 58.0 + 4.0 = 62.0
      mean       = (46 + 62) / 2 = 54.0
      dispersion = sqrt(((46-54)^2 + (62-54)^2) / 2) = 8.0
      spread     = max(1.0, 8.0, 3.0/sqrt(2) = 2.12) = 8.0

    The centre lands between them, and the spread reports honestly that the
    evidence does not agree — which is what propagates into a low
    recommendation confidence (PRD §6.2).
    """
    profile = build_fit_profile(
        closet(
            garment(
                "loose one",
                chest=50.0,
                rating=OverallRating.LOVE,
                signals=[SignalSnapshot("chest", Verdict.TOO_LOOSE)],
            ),
            garment(
                "tight one",
                chest=58.0,
                rating=OverallRating.LOVE,
                signals=[SignalSnapshot("chest", Verdict.TOO_TIGHT)],
            ),
        )
    )

    assert profile.maturity is ProfileMaturity.COLD_START
    assert profile.sample_size == 2
    assert dict(profile.dimensions) == {
        "chest": DimensionStat(preferred_cm=54.0, spread_cm=8.0, sample_size=2)
    }


def test_agreeing_signals_keep_the_spread_at_the_sample_size_floor() -> None:
    """Same closet size as the contradictory fixture, same centre — the only
    difference is that the evidence agrees, so the spread collapses to the
    sample-size term: 3.0 / sqrt(2) = 2.12.
    """
    profile = build_fit_profile(
        closet(
            garment(
                "a",
                chest=54.0,
                rating=OverallRating.LOVE,
                signals=[SignalSnapshot("chest", Verdict.PREFERRED)],
            ),
            garment(
                "b",
                chest=54.0,
                rating=OverallRating.LOVE,
                signals=[SignalSnapshot("chest", Verdict.PREFERRED)],
            ),
        )
    )

    assert dict(profile.dimensions) == {
        "chest": DimensionStat(preferred_cm=54.0, spread_cm=2.12, sample_size=2)
    }


def test_disliked_garment_pulls_less_than_a_loved_one() -> None:
    """PRD §6.2's "dislike + too tight" case. v1 stores no hard bound (see the
    module docstring), but the garment still pushes the preference away from
    itself, and its low rating weight keeps it from dominating a loved one.

      loved:    54.0, no signal              → 54.0 at weight 1.0
      disliked: 50.0, "too tight" (+4.0)     → 54.0 at weight 0.2

    Both imply 54.0 here, so the centre is 54.0 with zero dispersion — the
    point is that a disliked garment's *measurement* does not drag the
    profile down to 50.0.
    """
    profile = build_fit_profile(
        closet(
            garment("loved", chest=54.0, rating=OverallRating.LOVE),
            garment(
                "disliked",
                chest=50.0,
                rating=OverallRating.DISLIKE,
                signals=[SignalSnapshot("chest", Verdict.TOO_TIGHT)],
            ),
        )
    )

    assert profile.dimensions["chest"].preferred_cm == 54.0
    # A disliked garment is never cited as a reference (PRD §5.4).
    assert [ref.label for ref in profile.references] == ["loved"]


# ---------------------------------------------------------------------------
# Fixture 4 — use-case-specific signals (gym vs office).
# ---------------------------------------------------------------------------


def test_use_case_signals_build_per_use_case_variants() -> None:
    """PRD §6.2: "preferred for casual but slightly short for layering creates
    a context-dependent preference."

    One loved garment, chest 54.0 and sleeve 63.0, with three signals:
      untagged  sleeve "preferred"            →  63.0
      office    sleeve "too short"   (+4.0)   →  67.0
      gym       chest  "slightly tight" (+1.5) → 55.5

    Default profile uses only the untagged signal. The office variant
    overrides sleeve and keeps the default chest; the gym variant overrides
    chest and keeps the untagged sleeve verdict.
    """
    profile = build_fit_profile(
        closet(
            garment(
                "Oxford",
                measurements={"chest": 54.0, "sleeve_length": 63.0},
                rating=OverallRating.LOVE,
                use_cases=["office", "gym"],
                signals=[
                    SignalSnapshot("sleeve_length", Verdict.PREFERRED),
                    SignalSnapshot("sleeve_length", Verdict.TOO_SHORT, use_case="office"),
                    SignalSnapshot("chest", Verdict.SLIGHTLY_TIGHT, use_case="gym"),
                ],
            )
        )
    )

    assert dict(profile.dimensions) == {
        "chest": DimensionStat(54.0, 3.0, 1),
        "sleeve_length": DimensionStat(63.0, 3.0, 1),
    }
    assert set(profile.use_case_variants) == {"office", "gym"}
    assert dict(profile.use_case_variants["office"]) == {
        "chest": DimensionStat(54.0, 3.0, 1),
        "sleeve_length": DimensionStat(67.0, 3.0, 1),
    }
    assert dict(profile.use_case_variants["gym"]) == {
        "chest": DimensionStat(55.5, 3.0, 1),
        "sleeve_length": DimensionStat(63.0, 3.0, 1),
    }


def test_use_case_tagged_signal_does_not_move_the_default_profile() -> None:
    """A verdict the user gave about the gym must not silently become their
    everyday preference."""
    untagged = build_fit_profile(closet(garment("a", chest=54.0, rating=OverallRating.LOVE)))
    tagged = build_fit_profile(
        closet(
            garment(
                "a",
                chest=54.0,
                rating=OverallRating.LOVE,
                signals=[SignalSnapshot("chest", Verdict.SLIGHTLY_TIGHT, use_case="gym")],
            )
        )
    )

    assert dict(tagged.dimensions) == dict(untagged.dimensions)
    assert tagged.use_case_variants["gym"]["chest"].preferred_cm == 55.5


def test_no_variants_without_use_case_tagged_signals() -> None:
    """Tagging a *garment* "work" says where it is worn; only a tagged
    *signal* says the fit verdict differs there. A variant cloned from the
    default profile would be noise."""
    profile = build_fit_profile(
        closet(
            garment(
                "a",
                chest=54.0,
                rating=OverallRating.LOVE,
                use_cases=["work", "casual"],
                signals=[SignalSnapshot("chest", Verdict.PREFERRED)],
            )
        )
    )

    assert profile.use_case_variants == {}


def test_variants_reach_the_wire_shape() -> None:
    response = build_fit_profile(
        closet(
            garment(
                "a",
                chest=54.0,
                rating=OverallRating.LOVE,
                signals=[SignalSnapshot("chest", Verdict.SLIGHTLY_TIGHT, use_case="office")],
            )
        )
    ).to_response()

    assert response.use_case_variants["office"]["chest"].preferred_cm == 55.5
    assert response.dimensions["chest"].preferred_cm == 54.0


# ---------------------------------------------------------------------------
# Fixture 5 — maturity.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, ProfileMaturity.COLD_START),
        (1, ProfileMaturity.COLD_START),
        (2, ProfileMaturity.COLD_START),
        # PRD §6.2: "with only 3 garments … ranges are wide and confidence low".
        (3, ProfileMaturity.DEVELOPING),
        (14, ProfileMaturity.DEVELOPING),
        # PRD §6.2: "with 15+ garments, ranges narrow and confidence rises".
        (15, ProfileMaturity.MATURE),
        (40, ProfileMaturity.MATURE),
    ],
)
def test_maturity_bands(count: int, expected: ProfileMaturity) -> None:
    profile = build_fit_profile(
        closet(*(garment(f"g{i}", chest=54.0, rating=OverallRating.LOVE) for i in range(count)))
    )

    assert profile.maturity is expected
    assert profile.sample_size == count


def test_spread_narrows_as_the_closet_grows() -> None:
    """The uncertainty that PRD §6.2 says must propagate into recommendation
    confidence. Identical garments, so dispersion is zero throughout and the
    sample-size term is the whole story: 3.0 / sqrt(n)."""

    def spread(count: int) -> float:
        return (
            build_fit_profile(
                closet(
                    *(garment(f"g{i}", chest=54.0, rating=OverallRating.LOVE) for i in range(count))
                )
            )
            .dimensions["chest"]
            .spread_cm
        )

    assert spread(1) == 3.0
    assert spread(4) == 1.5
    # Floored at MIN_SPREAD_CM — v1 never claims tighter certainty than 1 cm.
    assert spread(16) == 1.0
    assert spread(40) == 1.0


# ---------------------------------------------------------------------------
# The weak prior (PRD §6.2).
# ---------------------------------------------------------------------------


def test_stated_preference_steers_a_thin_closet() -> None:
    """One loved 54.0 cm garment, and the user said "slim" (prior 52.0):

    mean = (1.0 * 54.0 + 0.35 * 52.0) / 1.35 = 72.2 / 1.35 = 53.48
    """
    profile = build_fit_profile(
        closet(
            garment("a", chest=54.0, rating=OverallRating.LOVE),
            stated=StatedFitPreference.SLIM,
        )
    )

    assert profile.dimensions["chest"].preferred_cm == 53.74
    # ``sample_size`` counts garments, never the prior.
    assert profile.dimensions["chest"].sample_size == 1


def test_prior_is_swamped_by_a_mature_closet() -> None:
    """The PRD's word is "weak". With 15 loved garments the prior holds
    0.15 / (15 + 0.15) ≈ 1% of the weight, so a "relaxed" answer moves a 54 cm
    posterior by well under a millimetre."""
    garments = [garment(f"g{i}", chest=54.0, rating=OverallRating.LOVE) for i in range(15)]

    without = build_fit_profile(closet(*garments)).dimensions["chest"].preferred_cm
    with_prior = (
        build_fit_profile(closet(*garments, stated=StatedFitPreference.RELAXED))
        .dimensions["chest"]
        .preferred_cm
    )

    assert without == 54.0
    assert abs(with_prior - without) < 0.1


def test_prior_weight_is_weaker_than_the_weakest_real_evidence() -> None:
    """A single measured garment must outvote what the user said at signup,
    even an unrated one — the closet is the evidence, the answer is a hint."""
    assert min(RATING_WEIGHTS.values()) > PRIOR_WEIGHT


def test_prior_moves_the_centre_but_not_the_spread_at_tuned_gaps() -> None:
    """The prior shifts ``preferred_cm``; it does not widen ``spread_cm``.

    It does feed the dispersion term, but the slim/regular/relaxed columns sit
    2-3 cm apart, and at that distance the dispersion it adds stays under the
    ``BASE_SPREAD_CM / sqrt(n)`` floor at every closet size. Pinning this
    stops the module docstring from over-claiming: uncertainty here comes from
    closet disagreement and closet size, not from the onboarding answer.
    """
    garments = [garment(f"g{i}", chest=54.0, rating=OverallRating.LOVE) for i in range(4)]

    agreeing = build_fit_profile(closet(*garments, stated=StatedFitPreference.REGULAR)).dimensions[
        "chest"
    ]
    disagreeing = build_fit_profile(closet(*garments, stated=StatedFitPreference.SLIM)).dimensions[
        "chest"
    ]

    # "regular" prior is 54.0 — exactly the closet, so it changes nothing.
    assert agreeing == DimensionStat(54.0, BASE_SPREAD_CM / 2, 4)
    assert disagreeing.preferred_cm < agreeing.preferred_cm
    assert disagreeing.spread_cm == agreeing.spread_cm


def test_a_closet_far_from_the_stated_preference_does_widen_the_spread() -> None:
    """The dispersion path is reachable, just not at ordinary gaps: a user who
    answered "slim" (52.0 cm prior) but owns only 70 cm shirts is genuinely
    harder to predict, and the spread should say so."""
    garments = [garment(f"g{i}", chest=70.0, rating=OverallRating.LOVE) for i in range(4)]

    without = build_fit_profile(closet(*garments)).dimensions["chest"].spread_cm
    with_prior = (
        build_fit_profile(closet(*garments, stated=StatedFitPreference.SLIM))
        .dimensions["chest"]
        .spread_cm
    )

    assert without == BASE_SPREAD_CM / 2
    assert with_prior > without


def test_prior_only_touches_dimensions_the_closet_has() -> None:
    """A user with a stated preference and one chest measurement gets a chest
    preference, not the full six-dimension prior table."""
    profile = build_fit_profile(
        closet(
            garment("a", chest=54.0, rating=OverallRating.LOVE),
            stated=StatedFitPreference.RELAXED,
        )
    )

    assert set(profile.dimensions) == {"chest"}


def test_prior_table_covers_every_stated_preference() -> None:
    assert set(PRIOR_MEASUREMENTS_CM) == set(StatedFitPreference)


def test_prior_table_covers_every_button_down_dimension() -> None:
    """The prior is only useful where the category has a dimension, and a
    dimension with no prior silently falls back to evidence-only."""
    schema_dimensions = {entry["name"] for entry in _BUTTON_DOWN_MEASUREMENT_SCHEMA["dimensions"]}

    for preference, priors in PRIOR_MEASUREMENTS_CM.items():
        assert set(priors) == schema_dimensions, preference


def test_prior_values_fall_inside_the_category_validation_ranges() -> None:
    """A prior outside the range the closet endpoint accepts would pull the
    posterior toward a measurement no user could ever have entered."""
    bounds = {
        entry["name"]: (entry["min_cm"], entry["max_cm"])
        for entry in _BUTTON_DOWN_MEASUREMENT_SCHEMA["dimensions"]
    }

    for preference, priors in PRIOR_MEASUREMENTS_CM.items():
        for dimension, value in priors.items():
            low, high = bounds[dimension]
            assert low <= value <= high, (preference, dimension, value)


def test_prior_ordering_is_slim_then_regular_then_relaxed() -> None:
    """Whatever the tuned values, the three cuts must stay ordered on every
    dimension — a "slim" prior roomier than "relaxed" would be a typo that no
    single-value assertion would catch."""
    for dimension in PRIOR_MEASUREMENTS_CM[StatedFitPreference.REGULAR]:
        slim = PRIOR_MEASUREMENTS_CM[StatedFitPreference.SLIM][dimension]
        regular = PRIOR_MEASUREMENTS_CM[StatedFitPreference.REGULAR][dimension]
        relaxed = PRIOR_MEASUREMENTS_CM[StatedFitPreference.RELAXED][dimension]

        assert slim < regular < relaxed, dimension


def test_prior_table_is_immutable() -> None:
    """Hand-tuned constants, not runtime state — same rule as the stretch
    coefficients (CLAUDE.md gotchas)."""
    with pytest.raises(TypeError):
        PRIOR_MEASUREMENTS_CM[StatedFitPreference.SLIM]["chest"] = 99.0  # type: ignore[index]


# ---------------------------------------------------------------------------
# Stretch interaction (PRD §6.3).
# ---------------------------------------------------------------------------


def test_stretch_raises_the_effective_measurement() -> None:
    """A stretchy shirt that fits well implies the user tolerates a larger
    *effective* measurement: 54.0 + 3.5 (high stretch) = 57.5."""
    profile = build_fit_profile(
        closet(garment("a", chest=54.0, rating=OverallRating.LOVE, stretch=StretchLevel.HIGH))
    )

    assert dict(profile.dimensions) == {
        "chest": DimensionStat(preferred_cm=57.5, spread_cm=3.0, sample_size=1)
    }


def test_unrated_garment_uses_the_neutral_weight() -> None:
    """An unrated garment still counts, at the neutral weight — most garments
    are entered before the user has an opinion about them.

      loved 56.0 at 1.0 + unrated 50.0 at 0.5 → (56 + 25) / 1.5 = 54.0
    """
    profile = build_fit_profile(
        closet(
            garment("loved", chest=56.0, rating=OverallRating.LOVE),
            garment("unrated", chest=50.0, rating=None),
        )
    )

    assert profile.dimensions["chest"].preferred_cm == 54.0


def test_every_rating_weight_is_positive() -> None:
    """The invariant behind ``_dimension_stats``'s zero-weight guard.

    A zero weight would make a dimension vanish from the profile rather than
    fail loudly, so the guard stays — but this is what keeps it unreachable.
    Zeroing a rating is a deliberate modelling decision, not a tuning tweak.
    """
    assert all(weight > 0 for weight in RATING_WEIGHTS.values())


def test_rating_weights_are_ordered_by_how_much_the_user_liked_the_garment() -> None:
    """PRD §6.2 anchors the preferred range on loved garments, so the ordering
    love > like > tolerable > dislike is the model, not an accident of tuning."""
    assert (
        RATING_WEIGHTS[OverallRating.LOVE]
        > RATING_WEIGHTS[OverallRating.LIKE]
        > RATING_WEIGHTS[OverallRating.TOLERABLE]
        > RATING_WEIGHTS[OverallRating.DISLIKE]
    )


def test_snapshot_field_order_is_stable() -> None:
    """These dataclasses are constructed positionally in places, so inserting
    a field in the middle silently rebinds arguments rather than failing.

    ``garment_id`` was added to all three in TKT-P1-16 and belongs last for
    exactly that reason. Pinning the order turns a future mid-list insertion
    into a failing test instead of a profile built from shifted data.
    """
    assert [f.name for f in fields(GarmentSnapshot)] == [
        "label",
        "brand",
        "size_label",
        "measurements_cm",
        "stretch_level",
        "overall_rating",
        "use_cases",
        "signals",
        "garment_id",
    ]
    assert [f.name for f in fields(ReferenceGarment)] == [
        "label",
        "brand",
        "size_label",
        "overall_rating",
        "measurements_cm",
        "use_cases",
        "garment_id",
    ]
