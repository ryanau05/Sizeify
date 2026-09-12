"""Unit tests for recommendation assembly (TKT-P1-15) — PRD §5.4, §6.4.

The ticket's three acceptance cases: a high-confidence profile returns one
candidate, a ~45% one returns two with trade-off strings, and a cold-start
profile is clamped to ≤ 50%.

Confidence values are pinned exactly rather than compared against the
threshold, because the whole deliverable is the confidence *calculation* —
an assertion that merely brackets it would survive a change in the weighting
that moved every real recommendation across the two-candidate line.
"""

from __future__ import annotations

import pytest

from api.domain.fit_profile import DimensionStat, FitProfile, ReferenceGarment
from api.domain.matching import BrandProduct, MissingDimensionError, match
from api.domain.recommendation import (
    COLD_START_CONFIDENCE_CAP,
    CONFIDENCE_TWO_CANDIDATE_THRESHOLD,
    InsufficientClosetDataError,
    Recommendation,
    recommend,
)
from api.schemas.enums import OverallRating, ProfileMaturity

CATEGORY = "mens_button_down_shirt"

LOVED = ReferenceGarment(
    "Uniqlo Oxford", "uniqlo", "M", OverallRating.LOVE, {"chest": 54, "shoulder_width": 46.0}, ()
)
TOLERATED = ReferenceGarment(
    "Old Gap", "gap", "L", OverallRating.TOLERABLE, {"chest": 60, "shoulder_width": 48.0}, ()
)


def profile(
    maturity: ProfileMaturity = ProfileMaturity.DEVELOPING,
    *,
    spread: float = 2.0,
    references: tuple[ReferenceGarment, ...] = (LOVED, TOLERATED),
) -> FitProfile:
    return FitProfile(
        category_id=CATEGORY,
        maturity=maturity,
        sample_size=15 if maturity is ProfileMaturity.MATURE else 5,
        dimensions={
            "chest": DimensionStat(54, spread, 5),
            "shoulder_width": DimensionStat(46.0, spread, 5),
        },
        references=references,
    )


# A chart whose sizes are far apart, so the best one wins decisively.
SEPARATED = BrandProduct(
    brand="jcrew",
    product_name="Bowery Wrinkle-Free Dress Shirt",
    size_chart={
        "S": {"chest": 42, "shoulder_width": 42.0},
        "M": {"chest": 54, "shoulder_width": 46.0},
        "XL": {"chest": 67, "shoulder_width": 51.0},
    },
)

# Two adjacent sizes that straddle the preferred value — a genuine toss-up.
TOSS_UP = BrandProduct(
    brand="everlane",
    product_name="Relaxed Poplin",
    size_chart={
        "M": {"chest": 51, "shoulder_width": 44.5},
        "L": {"chest": 57, "shoulder_width": 47.5},
    },
)


def assert_four_components(rec: Recommendation) -> None:
    """PRD §5.4: size, confidence, fit notes, reference garments — all four,
    on every recommendation, without exception."""
    assert rec.primary.size_label
    assert 0.0 <= rec.confidence <= 1.0
    assert rec.primary.fit_notes
    assert rec.reference_garments
    assert all(ref.why for ref in rec.reference_garments)


# ---------------------------------------------------------------------------
# High confidence → one candidate.
# ---------------------------------------------------------------------------


def test_high_confidence_returns_a_single_candidate() -> None:
    """Mature profile, best size dead-on, runner-ups far away → 0.96.

    closeness = 1.0 (best is fully within spread, distance 0)
    gap       = distance(next best) → gap_score near 1
    certainty = 1.0 (mature)
    """
    rec = recommend(profile(ProfileMaturity.MATURE), SEPARATED)

    assert rec.primary.size_label == "M"
    assert rec.confidence == 0.96
    assert rec.confidence >= CONFIDENCE_TWO_CANDIDATE_THRESHOLD
    assert rec.alternate is None
    assert rec.primary.tradeoff is None
    assert_four_components(rec)


def test_high_confidence_fit_notes_name_every_scored_dimension() -> None:
    rec = recommend(profile(ProfileMaturity.MATURE), SEPARATED)

    assert rec.primary.fit_notes == [
        "Chest: right in your preferred range.",
        "Shoulders: right in your preferred range.",
    ]


def test_brand_and_product_travel_with_the_recommendation() -> None:
    rec = recommend(profile(ProfileMaturity.MATURE), SEPARATED)

    assert rec.brand == "jcrew"
    assert rec.product_name == "Bowery Wrinkle-Free Dress Shirt"


# ---------------------------------------------------------------------------
# Low confidence → two candidates with trade-offs.
# ---------------------------------------------------------------------------


def test_low_confidence_returns_two_candidates_with_tradeoffs() -> None:
    """Developing profile, two adjacent sizes straddling the preferred value
    with a tight spread → 0.47, below the 60% two-candidate threshold."""
    rec = recommend(profile(ProfileMaturity.DEVELOPING, spread=0.5), TOSS_UP)

    assert rec.confidence == 0.47
    assert rec.confidence < CONFIDENCE_TWO_CANDIDATE_THRESHOLD

    assert rec.primary.size_label == "M"
    assert rec.alternate is not None
    assert rec.alternate.size_label == "L"

    # PRD §5.4: two candidates *with trade-offs* — both sides need one, or the
    # user has no basis for choosing between them.
    assert rec.primary.tradeoff
    assert rec.alternate.tradeoff
    assert "M" in rec.primary.tradeoff and "L" in rec.primary.tradeoff

    # The alternate is a full candidate, not a bare size label.
    assert rec.alternate.fit_notes
    assert_four_components(rec)


def test_alternate_tradeoff_names_the_direction() -> None:
    """The runner-up sits above the preferred value on both dimensions, so it
    is the roomier option and the string has to say so."""
    rec = recommend(profile(ProfileMaturity.DEVELOPING, spread=0.5), TOSS_UP)

    assert rec.alternate is not None
    assert "roomier" in (rec.alternate.tradeoff or "")


def test_alternate_tradeoff_says_slimmer_when_the_runner_up_is_smaller() -> None:
    slimmer_runner_up = BrandProduct(
        "b",
        "p",
        {
            "L": {"chest": 57, "shoulder_width": 47.5},
            "M": {"chest": 50.5, "shoulder_width": 44.0},
        },
    )
    rec = recommend(profile(ProfileMaturity.DEVELOPING, spread=0.5), slimmer_runner_up)

    assert rec.alternate is not None
    assert "slimmer" in (rec.alternate.tradeoff or "")


def test_no_alternate_when_the_chart_offers_only_one_size() -> None:
    """Low confidence with nothing to compare against: there is no second
    candidate to offer, and inventing one is not an option."""
    rec = recommend(
        profile(ProfileMaturity.COLD_START),
        BrandProduct("b", "p", {"M": {"chest": 54, "shoulder_width": 46.0}}),
    )

    assert rec.confidence < CONFIDENCE_TWO_CANDIDATE_THRESHOLD
    assert rec.alternate is None
    assert_four_components(rec)


def test_threshold_is_exclusive() -> None:
    """Confidence exactly at the threshold is *not* low confidence — the PRD
    says "below 60%", and a boundary that drifts changes which
    recommendations show two sizes."""
    assert CONFIDENCE_TWO_CANDIDATE_THRESHOLD == 0.60


# ---------------------------------------------------------------------------
# Cold start.
# ---------------------------------------------------------------------------


def test_cold_start_confidence_is_capped() -> None:
    """Same closet, same product, three maturities: the cold-start answer is
    clamped to the cap even though its closeness and gap would earn more."""
    uncapped = recommend(profile(ProfileMaturity.MATURE), SEPARATED).confidence
    developing = recommend(profile(ProfileMaturity.DEVELOPING), SEPARATED).confidence
    cold = recommend(profile(ProfileMaturity.COLD_START), SEPARATED).confidence

    assert cold == COLD_START_CONFIDENCE_CAP == 0.50
    assert cold < developing < uncapped


def test_cold_start_always_offers_two_candidates() -> None:
    """The cap sits below the two-candidate threshold, so a cold-start user
    is never shown a single size as though it were settled."""
    rec = recommend(profile(ProfileMaturity.COLD_START), SEPARATED)

    assert rec.confidence < CONFIDENCE_TWO_CANDIDATE_THRESHOLD
    assert rec.alternate is not None
    assert rec.alternate.tradeoff
    assert_four_components(rec)


def test_empty_profile_refuses_rather_than_guessing() -> None:
    """A profile with no scored dimensions cannot satisfy PRD §5.4 — there are
    no deltas to narrate — and every size would tie at distance 0, making the
    "best" one an artifact of size-chart ordering. PRD §13's branch is to tell
    the user to add shirts, not to name a size.
    """
    empty = FitProfile(CATEGORY, ProfileMaturity.COLD_START, 0, {}, ())
    product = BrandProduct(
        "jcrew", "Bowery", {"XL": {"chest": 67}, "S": {"chest": 43}, "M": {"chest": 54}}
    )

    with pytest.raises(InsufficientClosetDataError) as exc:
        recommend(empty, product)

    assert "jcrew" in str(exc.value)


def test_insufficient_closet_data_is_a_value_error() -> None:
    """Subclassing keeps existing ``except ValueError`` callers working while
    letting the share-sheet path route this to PRD §13's copy specifically."""
    assert issubclass(InsufficientClosetDataError, ValueError)


# ---------------------------------------------------------------------------
# Confidence behaviour.
# ---------------------------------------------------------------------------


def test_confidence_rises_with_profile_maturity() -> None:
    """ "This uncertainty propagates into the final recommendation confidence
    score" (PRD §6.2) — identical evidence, different closet depth."""
    developing = recommend(profile(ProfileMaturity.DEVELOPING), SEPARATED).confidence
    mature = recommend(profile(ProfileMaturity.MATURE), SEPARATED).confidence

    assert mature > developing


def test_confidence_rises_with_the_gap_to_the_runner_up() -> None:
    """PRD §6.4: "larger gap = higher confidence"."""
    close_rivals = BrandProduct(
        "b",
        "p",
        {
            "M": {"chest": 54, "shoulder_width": 46.0},
            "L": {"chest": 56, "shoulder_width": 47.0},
        },
    )
    far_rivals = BrandProduct(
        "b",
        "p",
        {
            "M": {"chest": 54, "shoulder_width": 46.0},
            "XXL": {"chest": 77, "shoulder_width": 56.0},
        },
    )
    prof = profile(ProfileMaturity.MATURE)

    assert recommend(prof, far_rivals).confidence > recommend(prof, close_rivals).confidence


def test_confidence_falls_as_the_best_size_drifts_out_of_range() -> None:
    prof = profile(ProfileMaturity.MATURE, spread=1.0)
    on_target = BrandProduct("b", "p", {"M": {"chest": 54, "shoulder_width": 46.0}})
    off_target = BrandProduct("b", "p", {"M": {"chest": 65, "shoulder_width": 51.0}})

    assert recommend(prof, off_target).confidence < recommend(prof, on_target).confidence


@pytest.mark.parametrize("maturity", list(ProfileMaturity))
def test_confidence_stays_within_zero_and_one(maturity: ProfileMaturity) -> None:
    rec = recommend(profile(maturity), SEPARATED)

    assert 0.0 <= rec.confidence <= 1.0
    # Two decimal places — it is rendered as a percentage (PRD §5.4).
    assert rec.confidence == round(rec.confidence, 2)


# ---------------------------------------------------------------------------
# Reference garments (PRD §5.4 component four).
# ---------------------------------------------------------------------------


def test_references_are_ranked_against_the_recommended_size() -> None:
    """PRD §6.4 compares "the recommended size's measurements directly to the
    user's most relevant owned garments", so which garment leads depends on
    what was recommended — not only on the profile.

    The loved Uniqlo M (107 cm) leads when M is recommended; the roomier Gap L
    (113 cm) leads when a 125 cm XXL is the only size on offer, despite its
    lower rating.
    """
    prof = profile(ProfileMaturity.MATURE)

    on_size = recommend(prof, BrandProduct("b", "p", {"M": {"chest": 54, "shoulder_width": 46.0}}))
    oversize = recommend(
        prof, BrandProduct("b", "p", {"XXL": {"chest": 72, "shoulder_width": 53.0}})
    )

    assert [ref.label for ref in on_size.reference_garments] == ["Uniqlo Oxford", "Old Gap"]
    assert [ref.label for ref in oversize.reference_garments] == ["Old Gap", "Uniqlo Oxford"]


def test_loved_garments_outrank_equally_close_ones() -> None:
    near_loved = ReferenceGarment(
        "Loved", "a", "M", OverallRating.LOVE, {"chest": 54, "shoulder_width": 46.0}, ()
    )
    near_unrated = ReferenceGarment(
        "Unrated", "b", "M", None, {"chest": 54, "shoulder_width": 46.0}, ()
    )
    rec = recommend(
        profile(ProfileMaturity.MATURE, references=(near_unrated, near_loved)), SEPARATED
    )

    assert [ref.label for ref in rec.reference_garments] == ["Loved", "Unrated"]


def test_reference_why_names_the_garment_and_its_rating() -> None:
    rec = recommend(profile(ProfileMaturity.MATURE), SEPARATED)
    why = rec.reference_garments[0].why

    assert "uniqlo" in why
    assert "love" in why


def test_at_most_two_reference_garments_are_cited() -> None:
    """PRD §5.4 wants at least one; a notification body has room for a couple,
    not a closet dump."""
    many = tuple(
        ReferenceGarment(
            f"g{i}", "b", "M", OverallRating.LIKE, {"chest": 54 + i, "shoulder_width": 46.0}, ()
        )
        for i in range(6)
    )
    rec = recommend(profile(ProfileMaturity.MATURE, references=many), SEPARATED)

    assert len(rec.reference_garments) == 2


# ---------------------------------------------------------------------------
# Fit-note narration.
# ---------------------------------------------------------------------------


def test_fit_notes_describe_direction_relative_to_the_preference() -> None:
    prof = profile(ProfileMaturity.MATURE, spread=1.0)

    roomy = recommend(prof, BrandProduct("b", "p", {"L": {"chest": 59, "shoulder_width": 46.0}}))
    slim = recommend(prof, BrandProduct("b", "p", {"S": {"chest": 49, "shoulder_width": 46.0}}))

    assert roomy.primary.fit_notes[0] == "Chest: about 5 cm roomier than you prefer."
    assert slim.primary.fit_notes[0] == "Chest: about 5 cm slimmer/shorter than you prefer."
    # The in-range dimension is narrated too — every scored dimension gets a note.
    assert roomy.primary.fit_notes[1] == "Shoulders: right in your preferred range."


# ---------------------------------------------------------------------------
# Wire shape and error propagation.
# ---------------------------------------------------------------------------


def test_wire_shape_of_a_confident_recommendation() -> None:
    wire = recommend(profile(ProfileMaturity.MATURE), SEPARATED).to_wire()

    assert set(wire) == {
        "brand",
        "product_name",
        "primary",
        "confidence",
        "reference_garments",
        # PRD §6.4 requires the assumed use case to be stated, not silently
        # applied, so it is always present — ``None`` means unconditioned.
        "use_case_assumed",
    }
    assert set(wire["primary"]) == {"size_label", "fit_notes"}  # no tradeoff key when unset
    assert "alternate" not in wire
    for ref in wire["reference_garments"]:
        assert set(ref) == {"label", "brand", "size_label", "why"}


def test_wire_shape_of_a_two_candidate_recommendation() -> None:
    wire = recommend(profile(ProfileMaturity.DEVELOPING, spread=0.5), TOSS_UP).to_wire()

    assert "alternate" in wire
    assert set(wire["primary"]) == {"size_label", "fit_notes", "tradeoff"}
    assert set(wire["alternate"]) == {"size_label", "fit_notes", "tradeoff"}


def test_empty_size_chart_raises() -> None:
    with pytest.raises(ValueError, match="no sizes"):
        recommend(profile(), BrandProduct("b", "p", {}))


def test_incomplete_size_chart_propagates_the_matching_error() -> None:
    """A broken scraper fixture must surface as itself, not as a
    recommendation built from whatever dimensions happened to be present."""
    with pytest.raises(MissingDimensionError):
        recommend(profile(), BrandProduct("b", "p", {"M": {"chest": 54}}))


def test_refuses_when_there_are_no_citable_reference_garments() -> None:
    """PRD §5.4 makes reference garments mandatory. A closet rated entirely
    "dislike" has none to cite (they are excluded from ``references``, rightly
    — citing a shirt the user hates as the reason for a size is worse than
    citing nothing), which left the recommendation two components short of the
    four it promises while still reporting 76% confidence."""
    profile_without_refs = FitProfile(
        category_id=CATEGORY,
        maturity=ProfileMaturity.DEVELOPING,
        sample_size=4,
        dimensions={"chest": DimensionStat(54.0, 1.5, 4)},
        references=(),
    )

    with pytest.raises(InsufficientClosetDataError, match="reference garments"):
        recommend(profile_without_refs, BrandProduct("b", "p", {"M": {"chest": 54.0}}))


# ---------------------------------------------------------------------------
# Use-case conditioning (PRD §6.4).
# ---------------------------------------------------------------------------


def _profile_with_gym_variant() -> FitProfile:
    """Chest runs 4 cm roomier at the gym than it does by default."""
    return FitProfile(
        category_id=CATEGORY,
        maturity=ProfileMaturity.MATURE,
        sample_size=6,
        dimensions={"chest": DimensionStat(54.0, 0.5, 6)},
        references=(
            ReferenceGarment("Gym tee", "b", "M", OverallRating.LOVE, {"chest": 54.0}, ("gym",)),
        ),
        use_case_variants={"gym": {"chest": DimensionStat(58.0, 0.5, 6)}},
    )


SIZES = BrandProduct("b", "p", {"M": {"chest": 54.0}, "L": {"chest": 58.0}})


def test_conditioning_on_a_use_case_changes_the_recommended_size() -> None:
    """The whole point of the variants, which nothing consumed until now:
    a profile that wants more room at the gym should get a bigger shirt.

    Exercised through ``match`` rather than ``recommend`` because ``recommend``
    with no hint falls back to the most common use case (PRD §6.4), which in a
    profile with one variant is that variant — correct behaviour, but it would
    hide the unconditioned baseline this test is comparing against.
    """
    profile_ = _profile_with_gym_variant()

    unconditioned = match(profile_, SIZES)
    gym = match(profile_, SIZES, use_case="gym")

    assert unconditioned[0].size_label == "M"
    assert gym[0].size_label == "L"


def test_no_hint_falls_back_to_the_most_common_use_case() -> None:
    """PRD §6.4: "the system uses the user's most common use case as the
    default but explicitly notes which use case was assumed"."""
    result = recommend(_profile_with_gym_variant(), SIZES)

    assert result.use_case_assumed == "gym"
    assert result.primary.size_label == "L"


def test_the_assumed_use_case_is_stated() -> None:
    """PRD §6.4: the system "explicitly notes which use case was assumed"."""
    assert recommend(_profile_with_gym_variant(), SIZES, use_case="gym").use_case_assumed == "gym"


def test_a_hint_with_no_variant_conditions_nothing_and_says_so() -> None:
    """Claiming a use case we have no evidence for would mislabel an
    unconditioned recommendation as a conditioned one."""
    result = recommend(_profile_with_gym_variant(), SIZES, use_case="scuba")

    assert result.use_case_assumed is None
    assert result.primary.size_label == "M"


def test_the_most_common_use_case_is_assumed_without_a_hint() -> None:
    """PRD §6.4's fallback. Only use cases that have a variant are candidates:
    conditioning on one the user tags but never gave a differing verdict for
    would return the unconditioned profile under a misleading label."""
    profile_ = _profile_with_gym_variant()

    assert profile_.default_use_case() == "gym"
    assert recommend(profile_, SIZES).use_case_assumed == "gym"


def test_a_profile_without_variants_assumes_nothing() -> None:
    assert profile(ProfileMaturity.MATURE).default_use_case() is None
    assert recommend(profile(ProfileMaturity.MATURE), SEPARATED).use_case_assumed is None


def test_fit_notes_describe_the_variant_that_was_matched() -> None:
    """The notes have to narrate the same numbers the ranking used, or the
    reasoning contradicts the size."""
    gym = recommend(_profile_with_gym_variant(), SIZES, use_case="gym")

    assert gym.primary.fit_notes == ["Chest: right in your preferred range."]
