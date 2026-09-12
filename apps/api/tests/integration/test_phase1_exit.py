"""Phase 1 exit-criterion test — TKT-P1-19.

From ``docs/PROJECT_PLAN.md``:

    an integration test seeds a closet with five garments, calls the matching
    engine against a fixture ``brand_product``, and asserts the recommendation
    contains size + confidence + fit notes + reference garments matching a
    hand-computed expectation.

This is the one test that exercises the whole Phase 1 stack in a single pass:
signup and JWT auth (P1-07/08) → closet writes and fit signals (P1-09/10) →
the DB→snapshot adapter (P1-13) → profile construction (P1-11/12) → matching
(P1-14) → confidence and candidate selection (P1-15) → persistence (P1-16).
Nothing is mocked and nothing is hand-fed to the domain: every input reaches
it the way a real request would.

The expected values below are computed by hand in HAND-COMPUTED EXPECTATION
rather than read back from the code. That is the point of the ticket — if a
weighting constant moves, this test should fail with a number that can be
traced to the line of arithmetic it broke, not quietly re-derive the new
answer and agree with it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.domain.matching import BrandProduct as MatchingProduct
from api.domain.matching import match
from api.domain.recommendation import recommend
from api.llm.versions import MANUAL_PROMPT_VERSION
from api.models import OwnedGarment, User
from api.repositories.fit_signals import FitSignalRepository
from api.repositories.owned_garments import OwnedGarmentRepository
from api.repositories.recommendations import RecommendationRepository
from api.schemas.enums import ProfileMaturity, StretchLevel
from api.seeds.garment_categories import (
    MENS_BUTTON_DOWN_SHIRT_ID,
    seed_garment_categories,
)
from api.services.fit_profile import build_user_fit_profile

GARMENTS = "/closet/garments"
PROFILE = "/closet/fit-profile"

# ===========================================================================
# HAND-COMPUTED EXPECTATION
# ===========================================================================
#
# Closet (all six dimensions measured; every garment stretch_level='none',
# so the PRD §6.3 offset is 0 and effective == measured):
#
#   #  garment                rating  w     chest  body   signal
#   1  Uniqlo Oxford          love    1.00  54.0   71.0   chest preferred,
#                                                         body_length preferred
#   2  Everlane Poplin        like    0.75  54.0   71.0   chest preferred
#   3  J.Crew Bowery          like    0.75  52.0   71.0   chest slightly_tight, 2.0 cm
#   4  Brooks Regent          like    0.75  54.0   74.0   body_length slightly_loose, 3.0 cm
#   5  Lululemon Commission   love    1.00  54.0   71.0   chest slightly_tight, 1.5 cm,
#                                                         use_case='gym'
#
#   shoulder_width 46.0, sleeve_length 63.0, neck_circumference 39.0 and
#   cuff_circumference 23.0 are identical on all five and carry no signals.
#   No stated_fit_preference, so the weak prior contributes nothing (its own
#   behaviour is pinned in tests/test_fit_profile.py).
#
# --- Profile (PRD §6.2) ----------------------------------------------------
#
#   Implied preferred value per garment = measured + verdict shift.
#   Garment 5's chest signal is tagged 'gym', so it is excluded from the
#   unconditioned profile.
#
#   chest:  #1 54.0 | #2 54.0 | #3 52.0+2.0=54.0 | #4 54.0 | #5 54.0
#           every garment implies 54.0  ->  preferred = 54.0, dispersion = 0
#   body:   #1 71.0 | #2 71.0 | #3 71.0 | #4 74.0-3.0=71.0 | #5 71.0
#           every garment implies 71.0  ->  preferred = 71.0, dispersion = 0
#   others: unanimous at their measured value.
#
#   spread = max(MIN_SPREAD_CM 1.0, dispersion 0, BASE_SPREAD_CM/sqrt(n))
#          = max(1.0, 0, 3.0/sqrt(5) = 1.3416) = 1.34
#   sample_size 5 -> DEVELOPING (3 <= 5 < 15), certainty 0.80.
#
#   gym variant chest: #5's tagged signal applies, the rest fall back to
#   their untagged verdicts:
#       (1.00*54.0 + 0.75*54.0 + 0.75*54.0 + 0.75*54.0 + 1.00*55.5) / 4.25
#     = 231.0 / 4.25 = 54.3529 -> 54.35
#
# --- Matching (PRD §6.4) ---------------------------------------------------
#
#   Weights: chest .35, shoulder .20, body .15, sleeve .15, neck .10, cuff .05
#   distance = sum over dimensions of weight * max(0, |delta| - spread)
#
#   size  chest  body  shoulder  sleeve  neck  cuff
#   S     50.0   68.0  44.0      61.0    37.0  22.0
#   M     54.0   71.0  46.0      63.0    41.0  23.0
#   L     58.0   74.0  48.0      65.0    42.0  24.0
#
#   M: only the neck is out of range.  |41.0-39.0| = 2.0
#      -> .10 * (2.0 - 1.34) = 0.066
#   S: .35*(4.0-1.34) + .15*(3.0-1.34) + .20*(2.0-1.34)
#      + .15*(2.0-1.34) + .10*(2.0-1.34) + .05*0    [cuff 1.0 < spread]
#      = 0.931 + 0.249 + 0.132 + 0.099 + 0.066 = 1.477
#   L: .35*(4.0-1.34) + .15*(3.0-1.34) + .20*(2.0-1.34)
#      + .15*(2.0-1.34) + .10*(3.0-1.34) + .05*0
#      = 0.931 + 0.249 + 0.132 + 0.099 + 0.166 = 1.577
#
#   ranking: M (0.066) < S (1.477) < L (1.577)
#
# --- Confidence (PRD §5.4 / §6.4) ------------------------------------------
#
#   closeness  = 1 / (1 + 0.6 * 0.066)      = 0.961908
#   gap        = 1.477 - 0.066              = 1.411
#   gap_score  = 1 - exp(-1.411 / 2)        = 0.506138
#   certainty  = 0.80                        (DEVELOPING)
#   base       = .45*0.961908 + .30*0.506138 + .25*0.80
#              = 0.432859 + 0.151841 + 0.200000 = 0.784700
#   -> 0.78, which is >= 0.60, so ONE candidate and no alternate.
#
# --- Reference garments (PRD §5.4) -----------------------------------------
#
#   relevance = sum |owned - recommended size's measurement| + rating bonus
#   The M candidate is the profile's preferred values with neck at 41.0, so
#   every garment contributes |39.0 - 41.0| = 2.0 from the neck alone.
#
#     #1 Uniqlo     2.0 + 0.0 chest - 2.0 (love) = 0.0   <- cited
#     #5 Lululemon  2.0 + 0.0 chest - 2.0 (love) = 0.0   <- cited
#     #2 Everlane   2.0 + 0.0 chest - 1.0 (like) = 1.0
#     #3 J.Crew     2.0 + 2.0 chest - 1.0 (like) = 3.0
#     #4 Brooks     2.0 + 3.0 body  - 1.0 (like) = 4.0
#
#   #1 and #5 tie at 0.0; the sort is stable, so closet order (oldest first)
#   decides. That is why the fixture pins created_at — see seed_closet.
#
# ===========================================================================

EXPECTED_SIZE = "M"
EXPECTED_CONFIDENCE = 0.78
EXPECTED_SPREAD_CM = 1.34
EXPECTED_PREFERRED_CM = {
    "chest": 54.0,
    "body_length": 71.0,
    "shoulder_width": 46.0,
    "sleeve_length": 63.0,
    "neck_circumference": 39.0,
    "cuff_circumference": 23.0,
}
EXPECTED_DISTANCES = {"M": 0.066, "S": 1.477, "L": 1.577}
EXPECTED_FIT_NOTES = {
    "Chest: right in your preferred range.",
    "Body length: right in your preferred range.",
    "Shoulders: right in your preferred range.",
    "Sleeve length: right in your preferred range.",
    "Cuff: right in your preferred range.",
    "Neck: about 2 cm roomier than you prefer.",
}
EXPECTED_REFERENCE_LABELS = ["Uniqlo Oxford", "Lululemon Commission"]

SIZE_CHART: dict[str, dict[str, float]] = {
    "S": {
        "chest": 50.0,
        "body_length": 68.0,
        "shoulder_width": 44.0,
        "sleeve_length": 61.0,
        "neck_circumference": 37.0,
        "cuff_circumference": 22.0,
    },
    "M": {
        "chest": 54.0,
        "body_length": 71.0,
        "shoulder_width": 46.0,
        "sleeve_length": 63.0,
        "neck_circumference": 41.0,
        "cuff_circumference": 23.0,
    },
    "L": {
        "chest": 58.0,
        "body_length": 74.0,
        "shoulder_width": 48.0,
        "sleeve_length": 65.0,
        "neck_circumference": 42.0,
        "cuff_circumference": 24.0,
    },
}

FIXTURE_PRODUCT = MatchingProduct(
    brand="J.Crew",
    product_name="Bowery Wrinkle-Free Dress Shirt",
    size_chart=SIZE_CHART,
    stretch_level=StretchLevel.NONE,
)


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-deploy-anywhere")
    yield


@pytest_asyncio.fixture(autouse=True)
async def _seeded_category(db_session: AsyncSession) -> AsyncIterator[None]:
    await seed_garment_categories(db_session)
    await db_session.flush()
    yield


def measurements(**overrides: float) -> dict[str, Any]:
    values = {
        "chest": 54.0,
        "body_length": 71.0,
        "shoulder_width": 46.0,
        "sleeve_length": 63.0,
        "neck_circumference": 39.0,
        "cuff_circumference": 23.0,
    }
    values.update(overrides)
    return {
        name: {"value": value, "unit": "cm", "source": "manual_tape"}
        for name, value in values.items()
    }


# The five garments of the fixture, in the order the user entered them.
# Covers the ticket's required mix: two preferred-fit, one slightly-tight
# chest, one slightly-loose body, one carrying a use-case-tagged signal.
CLOSET: list[dict[str, Any]] = [
    {
        "brand": "Uniqlo",
        "product_name": "Oxford",
        "overall_rating": "love",
        "measurements": measurements(),
        "signals": [
            {"dimension": "chest", "verdict": "preferred"},
            {"dimension": "body_length", "verdict": "preferred"},
        ],
    },
    {
        "brand": "Everlane",
        "product_name": "Poplin",
        "overall_rating": "like",
        "measurements": measurements(),
        "signals": [{"dimension": "chest", "verdict": "preferred"}],
    },
    {
        "brand": "J.Crew",
        "product_name": "Bowery",
        "overall_rating": "like",
        "measurements": measurements(chest=52.0),
        "signals": [{"dimension": "chest", "verdict": "slightly_tight", "magnitude_cm": 2.0}],
    },
    {
        "brand": "Brooks Brothers",
        "product_name": "Regent",
        "overall_rating": "like",
        "measurements": measurements(body_length=74.0),
        "signals": [{"dimension": "body_length", "verdict": "slightly_loose", "magnitude_cm": 3.0}],
    },
    {
        "brand": "Lululemon",
        "product_name": "Commission",
        "overall_rating": "love",
        "measurements": measurements(),
        "signals": [
            {
                "dimension": "chest",
                "verdict": "slightly_tight",
                "magnitude_cm": 1.5,
                "use_case": "gym",
            }
        ],
    },
]


async def seed_closet(
    client: AsyncClient, session: AsyncSession, *, first_stretch: str = "none"
) -> tuple[User, dict[str, str]]:
    """Sign up, then enter the five garments and their signals through the API.

    Written through the real endpoints rather than the ORM so the test covers
    the request path a user actually takes — validation, ownership scoping and
    the ``source='user_added'`` invariant included.

    ``created_at`` is then pinned to consecutive days. Postgres ``now()`` is
    transaction-start time, so every row written inside this test would
    otherwise share a timestamp and the closet's order would fall back to
    random UUIDs — which decides the tie between the two equally-relevant
    reference garments (see HAND-COMPUTED EXPECTATION). Real closets are built
    over days; the fixture just makes that explicit.
    """
    signup = await client.post(
        "/auth/signup",
        json={
            "email": "exit-criterion@example.com",
            "password": "Str0ng-Passphrase",
            "privacy_consent_accepted_at": "2026-01-01T09:00:00Z",
        },
    )
    assert signup.status_code == 201, signup.text
    headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    for day, entry in enumerate(CLOSET, start=1):
        created = await client.post(
            GARMENTS,
            json={
                "category_id": MENS_BUTTON_DOWN_SHIRT_ID,
                "brand": entry["brand"],
                "product_name": entry["product_name"],
                "size_label": "M",
                "measurements": entry["measurements"],
                "stretch_level": first_stretch if day == 1 else "none",
                "overall_rating": entry["overall_rating"],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        garment_id = created.json()["id"]

        for signal in entry["signals"]:
            posted = await client.post(
                f"{GARMENTS}/{garment_id}/fit-signals", json=signal, headers=headers
            )
            assert posted.status_code == 201, posted.text

        await session.execute(
            sa.update(OwnedGarment)
            .where(OwnedGarment.id == sa.cast(garment_id, sa.Uuid()))
            .values(created_at=datetime(2026, 1, day, tzinfo=UTC))
        )
    await session.flush()

    user = (
        await session.execute(sa.select(User).where(User.email == "exit-criterion@example.com"))
    ).scalar_one()
    return user, headers


# ---------------------------------------------------------------------------
# The exit criterion.
# ---------------------------------------------------------------------------


async def test_phase1_exit_criterion(client: AsyncClient, db_session: AsyncSession) -> None:
    """Five garments in, one fully-formed recommendation out."""
    user, _ = await seed_closet(client, db_session)

    profile = await build_user_fit_profile(
        user, OwnedGarmentRepository(db_session), FitSignalRepository(db_session)
    )

    # --- Profile matches the hand computation --------------------------
    assert profile.maturity is ProfileMaturity.DEVELOPING
    assert profile.sample_size == 5
    assert {name: stat.preferred_cm for name, stat in profile.dimensions.items()} == (
        EXPECTED_PREFERRED_CM
    )
    # Compared as dicts rather than with ``all(...)``: a bare ``assert False``
    # would say a constant drifted without saying to what, which is the whole
    # point of pinning the arithmetic above.
    assert {name: stat.spread_cm for name, stat in profile.dimensions.items()} == dict.fromkeys(
        EXPECTED_PREFERRED_CM, EXPECTED_SPREAD_CM
    )
    assert {name: stat.sample_size for name, stat in profile.dimensions.items()} == (
        dict.fromkeys(EXPECTED_PREFERRED_CM, 5)
    )
    # The gym-tagged signal conditions its own variant and nothing else.
    assert profile.use_case_variants["gym"]["chest"].preferred_cm == 54.35
    assert profile.dimensions["chest"].preferred_cm == 54.0

    # --- Ranking matches the hand computation --------------------------
    ranked = match(profile, FIXTURE_PRODUCT)
    assert [size.size_label for size in ranked] == ["M", "S", "L"]
    assert {size.size_label: size.distance for size in ranked} == EXPECTED_DISTANCES

    # --- All four PRD §5.4 components ----------------------------------
    recommendation = recommend(profile, FIXTURE_PRODUCT)

    # (1) Size.
    assert recommendation.primary.size_label == EXPECTED_SIZE
    # (2) Confidence — above the 60% threshold, so a single candidate.
    assert recommendation.confidence == EXPECTED_CONFIDENCE
    assert recommendation.alternate is None
    # (3) Fit notes: one per scored dimension, naming the one that misses.
    assert set(recommendation.primary.fit_notes) == EXPECTED_FIT_NOTES
    assert len(recommendation.primary.fit_notes) == 6
    # (4) Reference garments: the two loved shirts closest to the M.
    assert [ref.label for ref in recommendation.reference_garments] == (EXPECTED_REFERENCE_LABELS)
    assert all(ref.why for ref in recommendation.reference_garments)
    assert all(ref.garment_id is not None for ref in recommendation.reference_garments)


async def test_exit_criterion_recommendation_persists(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The same recommendation, stored — PRD §12.2 correlates correctness
    against the closet months later, which only works if the row is complete."""
    user, _ = await seed_closet(client, db_session)
    from _factories import make_brand_product

    product_row = await make_brand_product(db_session, size_chart=SIZE_CHART)

    profile = await build_user_fit_profile(
        user, OwnedGarmentRepository(db_session), FitSignalRepository(db_session)
    )
    output = recommend(profile, FIXTURE_PRODUCT)
    stored = await RecommendationRepository(db_session).create_from(
        output, user_id=user.id, brand_product_id=product_row.id
    )

    assert stored.recommended_size == EXPECTED_SIZE
    assert stored.confidence == Decimal(str(EXPECTED_CONFIDENCE))
    assert set(stored.fit_notes["primary"]["fit_notes"]) == EXPECTED_FIT_NOTES
    assert len(stored.reference_garment_ids) == 2
    assert stored.prompt_version == MANUAL_PROMPT_VERSION
    assert stored.outcome == "pending"


async def test_exit_criterion_profile_is_served_over_http(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The same profile the engine consumed is what the app would render."""
    _, headers = await seed_closet(client, db_session)

    body = (await client.get(PROFILE, headers=headers)).json()

    assert body["maturity"] == ProfileMaturity.DEVELOPING.value
    assert body["sample_size"] == 5
    assert body["hint"] is None
    assert {
        name: stat["preferred_cm"] for name, stat in body["dimensions"].items()
    } == EXPECTED_PREFERRED_CM
    assert body["use_case_variants"]["gym"]["chest"]["preferred_cm"] == 54.35


async def test_stretch_reaches_the_exit_criterion_flow(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Guards the one engine input the required fixture leaves untouched.

    Every garment in the fixture above is ``stretch_level='none'``, so the
    PRD §6.3 coefficients contribute nothing to the headline expectation —
    a regression in them would not fail it. This runs the same closet with
    the Uniqlo shirt marked *moderate* stretch instead.

    Moderate is +2.0 cm (PRD §6.3's anchor), so that garment's effective
    chest becomes 56.0 and the posterior shifts:

        (1.00*56.0 + 0.75*54.0 + 0.75*54.0 + 0.75*54.0 + 1.00*54.0) / 4.25
      = 231.5 / 4.25 = 54.4706 -> 54.47

    A stretchy shirt the user loves means they tolerate a larger *effective*
    measurement, so the preference rises rather than falls.
    """
    user, _ = await seed_closet(client, db_session, first_stretch="moderate")

    profile = await build_user_fit_profile(
        user, OwnedGarmentRepository(db_session), FitSignalRepository(db_session)
    )

    assert profile.dimensions["chest"].preferred_cm == 54.47
    assert profile.dimensions["chest"].preferred_cm > EXPECTED_PREFERRED_CM["chest"]
