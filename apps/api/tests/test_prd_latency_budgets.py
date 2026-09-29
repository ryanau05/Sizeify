"""Wall-clock assertions on the PRD §9.2 per-step budgets.

PRD §9.2 breaks the share-sheet round trip into steps with a budget each —
resolve 200 ms, scraper 1.5 s, **fit-profile build 100 ms**, **matching
300 ms**, push 500 ms — and CLAUDE.md says to flag any change that risks
breaching them. Until now nothing measured any of them; the budgets were
checked only by reading the code, which is how a quadratic creeps in
behind a docstring that still claims linear.

Two of the five are testable today, and they are the two this repository
owns: profile construction and matching are pure functions in
``api.domain``. The other three are Phase 2 (scraper), Phase 5 (URL
resolve) and Phase 7 (push), and the end-to-end p50/p95 needs the
share-sheet endpoint that Phase 6 introduces — there is nothing to time
yet, which is recorded in TODOS.md rather than faked here.

PRD §10.1's "<4 minutes to first garment" is a UX measurement over a human
using the mobile client. It cannot be observed from the backend at all.

**Scale.** The budgets are not measured against a toy closet. PRD §10.1
asks for 3-5 garments at onboarding, but a profile is meant to sharpen
over a year of use, so these run at 50 garments with 6 signals each —
comfortably past any real closet, comfortably under the 500-garment cap.

**On flakiness.** These assert the budget itself rather than a padded
multiple, because the budget is the contract and there is a lot of room
against it: the build measures ~2 ms here against its 100 ms allowance. A
failure means something changed by an order of magnitude, not that the
runner was briefly busy.
"""

from __future__ import annotations

import time

import pytest

from api.domain.fit_profile import (
    ClosetSnapshot,
    GarmentSnapshot,
    SignalSnapshot,
    build_fit_profile,
)
from api.domain.matching import BrandProduct, match
from api.schemas.enums import OverallRating, StretchLevel, Verdict
from api.seeds.garment_categories import MENS_BUTTON_DOWN_SHIRT_ID

#: PRD §9.2, "profile build".
PROFILE_BUILD_BUDGET_MS = 100.0

#: PRD §9.2, "matching".
MATCHING_BUDGET_MS = 300.0

#: Past any real closet, well under ``max_closet_garments``.
GARMENT_COUNT = 50

DIMENSIONS = {
    "chest": 54.0,
    "body_length": 71.0,
    "shoulder_width": 46.0,
    "sleeve_length": 63.0,
    "neck_circumference": 39.0,
    "cuff_circumference": 23.0,
}


def _closet() -> ClosetSnapshot:
    """A mature closet: every garment measured, rated, and commented on."""
    verdicts = [Verdict.PREFERRED, Verdict.SLIGHTLY_TIGHT, Verdict.SLIGHTLY_LOOSE]
    use_cases = ["work", "weekend", "gym"]
    garments = [
        GarmentSnapshot(
            label=f"Shirt {index}",
            brand="Uniqlo",
            size_label="M",
            measurements_cm={name: value + (index % 5) * 0.5 for name, value in DIMENSIONS.items()},
            stretch_level=StretchLevel.SLIGHT,
            overall_rating=OverallRating.LOVE,
            use_cases=(use_cases[index % len(use_cases)],),
            signals=tuple(
                SignalSnapshot(
                    dimension=dimension,
                    verdict=verdicts[(index + position) % len(verdicts)],
                    use_case=use_cases[index % len(use_cases)] if position % 2 else None,
                )
                for position, dimension in enumerate(DIMENSIONS)
            ),
        )
        for index in range(GARMENT_COUNT)
    ]
    return ClosetSnapshot(MENS_BUTTON_DOWN_SHIRT_ID, garments)


def _size_chart() -> BrandProduct:
    return BrandProduct(
        brand="jcrew",
        product_name="Bowery Wrinkle-Free Dress Shirt",
        size_chart={
            label: {name: value + offset for name, value in DIMENSIONS.items()}
            for label, offset in (("S", -4.0), ("M", 0.0), ("L", 4.0), ("XL", 8.0))
        },
    )


@pytest.mark.slow
def test_fit_profile_build_stays_within_its_budget() -> None:
    """PRD §9.2: profile build ≤ 100 ms."""
    closet = _closet()

    start = time.perf_counter()
    profile = build_fit_profile(closet)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert profile.dimensions, "built nothing — the fixture is not exercising the path"
    assert elapsed_ms < PROFILE_BUILD_BUDGET_MS, (
        f"build_fit_profile took {elapsed_ms:.1f} ms for {GARMENT_COUNT} garments, "
        f"over PRD §9.2's {PROFILE_BUILD_BUDGET_MS:.0f} ms budget"
    )


@pytest.mark.slow
def test_matching_stays_within_its_budget() -> None:
    """PRD §9.2: matching ≤ 300 ms."""
    profile = build_fit_profile(_closet())
    product = _size_chart()

    start = time.perf_counter()
    ranked = match(profile, product)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert ranked, "ranked nothing — the fixture is not exercising the path"
    assert elapsed_ms < MATCHING_BUDGET_MS, (
        f"match took {elapsed_ms:.1f} ms against a {len(product.size_chart)}-size chart, "
        f"over PRD §9.2's {MATCHING_BUDGET_MS:.0f} ms budget"
    )


@pytest.mark.slow
def test_the_budgeted_path_end_to_end_leaves_headroom() -> None:
    """Both steps together, as one share-sheet request would run them.

    Their budgets are separate but they run back to back on the same
    request, so what matters operationally is the pair. 400 ms is the sum of
    the two §9.2 lines.
    """
    closet = _closet()
    product = _size_chart()

    start = time.perf_counter()
    ranked = match(build_fit_profile(closet), product)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert ranked
    assert elapsed_ms < PROFILE_BUILD_BUDGET_MS + MATCHING_BUDGET_MS, (
        f"profile build plus matching took {elapsed_ms:.1f} ms"
    )
