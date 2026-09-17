"""Integration tests for ``GET /closet/fit-profile`` — TKT-P1-13.

The ticket's acceptance: seed two garments plus signals and assert the
response matches ``build_fit_profile`` exactly.

The expected profile is built from a *hand-written* ``ClosetSnapshot``, not
from the service's own loader — comparing the endpoint against the same
adapter it uses would only prove the adapter is deterministic. Written this
way, the test fails if the adapter drops a signal, mangles a measurement, or
forgets the stated fit preference.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from _factories import make_user
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import jwt as auth_jwt
from api.config import get_settings
from api.domain.fit_profile import (
    ClosetSnapshot,
    GarmentSnapshot,
    SignalSnapshot,
    build_fit_profile,
)
from api.models import User
from api.repositories.fit_signals import FitSignalRepository
from api.repositories.owned_garments import OwnedGarmentRepository
from api.repositories.refresh_tokens import RefreshTokenRepository
from api.schemas.enums import (
    OverallRating,
    ProfileMaturity,
    StatedFitPreference,
    StretchLevel,
    Verdict,
)
from api.seeds.garment_categories import (
    MENS_BUTTON_DOWN_SHIRT_ID,
    seed_garment_categories,
)
from api.services.fit_profile import load_closet_snapshot

PROFILE = "/closet/fit-profile"
GARMENTS = "/closet/garments"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-deploy-anywhere")
    get_settings.cache_clear()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _seeded_category(db_session: AsyncSession) -> AsyncIterator[None]:
    await seed_garment_categories(db_session)
    await db_session.flush()
    yield


def measurements(**overrides: float) -> dict[str, Any]:
    full = {
        "chest": 54.0,
        "body_length": 71.0,
        "shoulder_width": 46.0,
        "sleeve_length": 63.0,
        "neck_circumference": 39.0,
        "cuff_circumference": 23.0,
    }
    full.update(overrides)
    return {
        name: {"value": value, "unit": "cm", "source": "manual_tape"}
        for name, value in full.items()
    }


def garment_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "category_id": MENS_BUTTON_DOWN_SHIRT_ID,
        "brand": "Uniqlo",
        "product_name": "Oxford",
        "size_label": "M",
        "measurements": measurements(),
        "stretch_level": "none",
        "overall_rating": "love",
        "use_cases": [],
    }
    body.update(overrides)
    return body


async def headers_for(session: AsyncSession, user: User) -> dict[str, str]:
    access, _ = await auth_jwt.issue_pair(user.id, RefreshTokenRepository(session))
    return {"Authorization": f"Bearer {access}"}


# ---------------------------------------------------------------------------
# The ticket's acceptance criterion.
# ---------------------------------------------------------------------------


async def test_response_matches_build_fit_profile_exactly(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Two garments plus signals, end to end."""
    user = await make_user(db_session, stated_fit_preference="regular")
    headers = await headers_for(db_session, user)

    first = await client.post(
        GARMENTS,
        json=garment_body(brand="Uniqlo", product_name="Oxford", measurements=measurements()),
        headers=headers,
    )
    second = await client.post(
        GARMENTS,
        json=garment_body(
            brand="J.Crew",
            product_name="Bowery",
            size_label="L",
            measurements=measurements(chest=57.0, sleeve_length=65.0),
            stretch_level="slight",
            overall_rating="like",
            use_cases=["work"],
        ),
        headers=headers,
    )
    assert first.status_code == second.status_code == 201

    for garment_id, signal in (
        (first.json()["id"], {"dimension": "chest", "verdict": "slightly_tight"}),
        (
            second.json()["id"],
            {"dimension": "sleeve_length", "verdict": "too_short", "magnitude_cm": 2.0},
        ),
    ):
        posted = await client.post(
            f"{GARMENTS}/{garment_id}/fit-signals", json=signal, headers=headers
        )
        assert posted.status_code == 201

    response = await client.get(PROFILE, headers=headers)
    assert response.status_code == 200

    expected = build_fit_profile(
        ClosetSnapshot(
            category_id=MENS_BUTTON_DOWN_SHIRT_ID,
            garments=[
                GarmentSnapshot(
                    label="Uniqlo Oxford",
                    brand="Uniqlo",
                    size_label="M",
                    measurements_cm={
                        "chest": 54.0,
                        "body_length": 71.0,
                        "shoulder_width": 46.0,
                        "sleeve_length": 63.0,
                        "neck_circumference": 39.0,
                        "cuff_circumference": 23.0,
                    },
                    stretch_level=StretchLevel.NONE,
                    overall_rating=OverallRating.LOVE,
                    signals=(SignalSnapshot("chest", Verdict.SLIGHTLY_TIGHT),),
                ),
                GarmentSnapshot(
                    label="J.Crew Bowery",
                    brand="J.Crew",
                    size_label="L",
                    measurements_cm={
                        "chest": 57.0,
                        "body_length": 71.0,
                        "shoulder_width": 46.0,
                        "sleeve_length": 65.0,
                        "neck_circumference": 39.0,
                        "cuff_circumference": 23.0,
                    },
                    stretch_level=StretchLevel.SLIGHT,
                    overall_rating=OverallRating.LIKE,
                    use_cases=("work",),
                    signals=(SignalSnapshot("sleeve_length", Verdict.TOO_SHORT, magnitude_cm=2.0),),
                ),
            ],
            stated_fit_preference=StatedFitPreference.REGULAR,
        )
    ).to_response()

    assert response.json() == expected.model_dump(mode="json")


async def test_signals_reach_the_profile_through_the_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The criterion TKT-P1-10 could only assert in-process: a signal posted
    to the API demonstrably moves the profile served by the API."""
    user = await make_user(db_session)
    headers = await headers_for(db_session, user)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)
    garment_id = created.json()["id"]

    before = (await client.get(PROFILE, headers=headers)).json()
    posted = await client.post(
        f"{GARMENTS}/{garment_id}/fit-signals",
        json={"dimension": "chest", "verdict": "slightly_tight", "magnitude_cm": 2.0},
        headers=headers,
    )
    assert posted.status_code == 201
    after = (await client.get(PROFILE, headers=headers)).json()

    assert before["dimensions"]["chest"]["preferred_cm"] == 54.0
    # "Slightly tight by 2 cm" means the user wants 2 cm more room.
    assert after["dimensions"]["chest"]["preferred_cm"] == 56.0


# ---------------------------------------------------------------------------
# Empty closet.
# ---------------------------------------------------------------------------


async def test_empty_closet_is_200_with_a_cold_start_hint(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """ "You have no profile yet" is a normal state for a new user, not a 404 —
    an error here would make onboarding look broken."""
    user = await make_user(db_session)

    response = await client.get(PROFILE, headers=await headers_for(db_session, user))

    assert response.status_code == 200
    body = response.json()
    assert body["maturity"] == ProfileMaturity.COLD_START.value
    assert body["sample_size"] == 0
    assert body["dimensions"] == {}
    assert body["use_case_variants"] == {}
    assert body["hint"]
    assert body["category_id"] == MENS_BUTTON_DOWN_SHIRT_ID


async def test_stated_preference_alone_does_not_manufacture_a_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The weak prior never invents a dimension (TKT-P1-12), so a user who
    answered onboarding but entered nothing still sees cold start."""
    user = await make_user(db_session, stated_fit_preference="relaxed")

    body = (await client.get(PROFILE, headers=await headers_for(db_session, user))).json()

    assert body["dimensions"] == {}
    assert body["maturity"] == ProfileMaturity.COLD_START.value


# ---------------------------------------------------------------------------
# Scoping and lifecycle.
# ---------------------------------------------------------------------------


async def test_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(PROFILE)).status_code == 401


async def test_profile_covers_only_the_authenticated_users_closet(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice = await make_user(db_session, email="alice@example.com")
    bob = await make_user(db_session, email="bob@example.com")
    alice_headers = await headers_for(db_session, alice)
    bob_headers = await headers_for(db_session, bob)

    assert (
        await client.post(GARMENTS, json=garment_body(), headers=alice_headers)
    ).status_code == 201

    alice_profile = (await client.get(PROFILE, headers=alice_headers)).json()
    bob_profile = (await client.get(PROFILE, headers=bob_headers)).json()

    assert alice_profile["sample_size"] == 1
    assert bob_profile["sample_size"] == 0
    assert bob_profile["dimensions"] == {}


async def test_deleted_garments_stop_shaping_the_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A tombstoned garment is kept so past recommendations stay explainable
    (TKT-P1-09), not so it keeps steering what the user prefers now."""
    user = await make_user(db_session)
    headers = await headers_for(db_session, user)
    keep = await client.post(GARMENTS, json=garment_body(), headers=headers)
    drop = await client.post(
        GARMENTS, json=garment_body(measurements=measurements(chest=70.0)), headers=headers
    )

    both = (await client.get(PROFILE, headers=headers)).json()
    assert both["sample_size"] == 2
    assert both["dimensions"]["chest"]["preferred_cm"] == 62.0  # midpoint of 54 and 70

    assert (
        await client.delete(f"{GARMENTS}/{drop.json()['id']}", headers=headers)
    ).status_code == 204
    remaining = (await client.get(PROFILE, headers=headers)).json()

    assert remaining["sample_size"] == 1
    assert remaining["dimensions"]["chest"]["preferred_cm"] == 54.0
    assert keep.status_code == 201


async def test_signals_on_a_deleted_garment_are_dropped_too(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Otherwise a removed garment's verdicts would keep shifting the profile
    with no measurement left to attach them to."""
    user = await make_user(db_session)
    headers = await headers_for(db_session, user)
    keeper = await client.post(GARMENTS, json=garment_body(), headers=headers)
    doomed = await client.post(GARMENTS, json=garment_body(brand="Doomed"), headers=headers)
    await client.post(
        f"{GARMENTS}/{doomed.json()['id']}/fit-signals",
        json={"dimension": "chest", "verdict": "too_tight", "magnitude_cm": 6.0},
        headers=headers,
    )

    await client.delete(f"{GARMENTS}/{doomed.json()['id']}", headers=headers)
    body = (await client.get(PROFILE, headers=headers)).json()

    assert body["sample_size"] == 1
    assert body["dimensions"]["chest"]["preferred_cm"] == 54.0
    assert keeper.status_code == 201


# ---------------------------------------------------------------------------
# Profile content.
# ---------------------------------------------------------------------------


async def test_maturity_reflects_closet_depth(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    headers = await headers_for(db_session, user)

    for index in range(3):
        assert (
            await client.post(GARMENTS, json=garment_body(brand=f"Brand {index}"), headers=headers)
        ).status_code == 201

    body = (await client.get(PROFILE, headers=headers)).json()

    assert body["maturity"] == ProfileMaturity.DEVELOPING.value
    assert body["sample_size"] == 3
    # Past cold start, so no banner.
    assert body["hint"] is None


async def test_use_case_variants_are_served(client: AsyncClient, db_session: AsyncSession) -> None:
    """PRD §6.2's context-dependent preference, all the way to the wire."""
    user = await make_user(db_session)
    headers = await headers_for(db_session, user)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)
    garment_id = created.json()["id"]

    for signal in (
        {"dimension": "sleeve_length", "verdict": "preferred"},
        {
            "dimension": "sleeve_length",
            "verdict": "too_short",
            "magnitude_cm": 3.0,
            "use_case": "layering",
        },
    ):
        assert (
            await client.post(f"{GARMENTS}/{garment_id}/fit-signals", json=signal, headers=headers)
        ).status_code == 201

    body = (await client.get(PROFILE, headers=headers)).json()

    assert body["dimensions"]["sleeve_length"]["preferred_cm"] == 63.0
    assert body["use_case_variants"]["layering"]["sleeve_length"]["preferred_cm"] == 66.0


async def test_every_measured_dimension_appears_with_spread_and_sample_size(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    headers = await headers_for(db_session, user)
    await client.post(GARMENTS, json=garment_body(), headers=headers)

    dimensions = (await client.get(PROFILE, headers=headers)).json()["dimensions"]

    assert set(dimensions) == {
        "chest",
        "body_length",
        "shoulder_width",
        "sleeve_length",
        "neck_circumference",
        "cuff_circumference",
    }
    for stat in dimensions.values():
        assert stat["preferred_cm"] > 0
        assert stat["spread_cm"] >= 0
        assert stat["sample_size"] == 1


async def test_stretch_is_applied_when_building_the_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """PRD §6.3: a stretchy shirt that fits well implies the user tolerates a
    larger effective measurement — 54.0 + 3.5 for high stretch."""
    user = await make_user(db_session)
    headers = await headers_for(db_session, user)
    await client.post(GARMENTS, json=garment_body(stretch_level="high"), headers=headers)

    body = (await client.get(PROFILE, headers=headers)).json()

    assert body["dimensions"]["chest"]["preferred_cm"] == 57.5


async def test_stated_fit_preference_steers_a_thin_closet(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The weak prior (PRD §6.2) reaches the endpoint: one 54 cm garment with
    a "slim" answer lands below 54, with "regular" it stays put."""
    slim = await make_user(db_session, email="slim@example.com", stated_fit_preference="slim")
    regular = await make_user(
        db_session, email="regular@example.com", stated_fit_preference="regular"
    )

    for user in (slim, regular):
        headers = await headers_for(db_session, user)
        assert (
            await client.post(GARMENTS, json=garment_body(), headers=headers)
        ).status_code == 201

    slim_body = (await client.get(PROFILE, headers=await headers_for(db_session, slim))).json()
    regular_body = (
        await client.get(PROFILE, headers=await headers_for(db_session, regular))
    ).json()

    assert slim_body["dimensions"]["chest"]["preferred_cm"] < 54.0
    assert regular_body["dimensions"]["chest"]["preferred_cm"] == 54.0


async def test_garment_without_a_product_name_is_labelled_by_brand_and_size(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """``product_name`` is optional (PRD §5.1 calls it free text), so the
    label falls back to brand plus size — "Your Uniqlo M" is what a push
    notification cites (PRD §5.4), and a blank there would read as a bug."""
    user = await make_user(db_session)
    headers = await headers_for(db_session, user)
    body = garment_body()
    del body["product_name"]
    assert (await client.post(GARMENTS, json=body, headers=headers)).status_code == 201

    snapshot = await load_closet_snapshot(
        user, OwnedGarmentRepository(db_session), FitSignalRepository(db_session)
    )

    assert [g.label for g in snapshot.garments] == ["Uniqlo M"]


async def test_malformed_stored_measurement_fails_loudly(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A row the adapter cannot parse must raise, not quietly contribute
    nothing.

    Skipping silently was the worst option available: the garment still
    counted toward ``sample_size`` and maturity but added no evidence, so a
    five-shirt closet returned ``maturity: "developing"`` with an empty
    ``dimensions`` map and no hint — a blank profile with no explanation
    anywhere. Reproduced against real legacy rows in the dev database.
    """
    import sqlalchemy as sa

    from api.models import OwnedGarment
    from api.services.fit_profile import MalformedMeasurementError, load_closet_snapshot

    user = await make_user(db_session)
    headers = await headers_for(db_session, user)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)
    await db_session.execute(
        sa.update(OwnedGarment)
        .where(OwnedGarment.id == sa.cast(created.json()["id"], sa.Uuid()))
        .values(measurements={"chest_cm": 54.0})  # the pre-Phase-1 flat shape
    )

    with pytest.raises(MalformedMeasurementError, match="chest_cm"):
        await load_closet_snapshot(
            user, OwnedGarmentRepository(db_session), FitSignalRepository(db_session)
        )


async def test_non_numeric_stored_measurement_fails_loudly(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The right shape with the wrong kind of value inside it.

    ``{"value": "54.0"}`` is what a JSON import that stringified its numbers
    leaves behind, and ``{"value": true}`` is what a bool sneaks through as —
    ``isinstance(True, int)`` is true, so a plain numeric check would accept
    it and feed 1.0 cm into the posterior. Both have to raise for the same
    reason the flat-shape row does: a garment that contributes no evidence
    still counts toward ``sample_size``, so the profile silently reports
    confidence it does not have.
    """
    import sqlalchemy as sa

    from api.models import OwnedGarment
    from api.services.fit_profile import MalformedMeasurementError, load_closet_snapshot

    user = await make_user(db_session)
    headers = await headers_for(db_session, user)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)
    garment_id = sa.cast(created.json()["id"], sa.Uuid())

    for bad_value in ("54.0", True):
        stored = measurements()
        stored["chest"]["value"] = bad_value
        await db_session.execute(
            sa.update(OwnedGarment).where(OwnedGarment.id == garment_id).values(measurements=stored)
        )

        with pytest.raises(MalformedMeasurementError, match="non-numeric"):
            await load_closet_snapshot(
                user, OwnedGarmentRepository(db_session), FitSignalRepository(db_session)
            )


def test_chest_guide_text_matches_the_declared_range() -> None:
    """PRD §5.1 says "pit-to-pit doubled" and §5.2 caps chest at 80 cm; a
    doubled medium is ~108. The range carries the number and the validator
    enforces it, so the guide must not tell users to double."""
    from api.seeds.garment_categories import _BUTTON_DOWN_MEASUREMENT_SCHEMA

    chest = next(d for d in _BUTTON_DOWN_MEASUREMENT_SCHEMA["dimensions"] if d["name"] == "chest")

    assert chest["max_cm"] == 80.0
    assert "do not double" in chest["guide"]
