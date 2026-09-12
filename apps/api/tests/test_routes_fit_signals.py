"""Integration tests for ``POST /closet/garments/{id}/fit-signals`` — TKT-P1-10.

The ticket's acceptance criteria:

* a signal is created with ``source='user_added'`` and reaches the fit
  profile
* unknown verdicts and unknown dimensions are rejected

On "retrievable via fit-profile endpoint": ``GET /closet/fit-profile`` is
TKT-P1-13 and does not exist yet, so
``test_signal_reaches_the_fit_profile`` asserts the substance of that
criterion instead — the persisted signal is fed through the real
``domain.fit_profile.build_fit_profile`` and demonstrably moves the
result. When TKT-P1-13 lands, that test is the one to re-point at the
endpoint.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from _factories import make_user
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import jwt as auth_jwt
from api.domain.fit_profile import (
    ClosetSnapshot,
    GarmentSnapshot,
    SignalSnapshot,
    build_fit_profile,
)
from api.models import FitSignal
from api.repositories.refresh_tokens import RefreshTokenRepository
from api.schemas.enums import OverallRating, StretchLevel, Verdict
from api.seeds.garment_categories import (
    MENS_BUTTON_DOWN_SHIRT_ID,
    seed_garment_categories,
)

GARMENTS = "/closet/garments"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-deploy-anywhere")
    yield


@pytest_asyncio.fixture(autouse=True)
async def _seeded_category(db_session: AsyncSession) -> AsyncIterator[None]:
    await seed_garment_categories(db_session)
    await db_session.flush()
    yield


def measurements() -> dict[str, Any]:
    return {
        name: {"value": value, "unit": "cm", "source": "manual_tape"}
        for name, value in (
            ("chest", 54.0),
            ("body_length", 71.0),
            ("shoulder_width", 46.0),
            ("sleeve_length", 63.0),
            ("neck_circumference", 39.0),
            ("cuff_circumference", 23.0),
        )
    }


async def authed_garment(client: AsyncClient, session: AsyncSession) -> tuple[dict[str, str], str]:
    """A user, an access token for them, and a garment in their closet."""
    user = await make_user(session)
    access, _ = await auth_jwt.issue_pair(user.id, RefreshTokenRepository(session))
    headers = {"Authorization": f"Bearer {access}"}

    created = await client.post(
        GARMENTS,
        json={
            "category_id": MENS_BUTTON_DOWN_SHIRT_ID,
            "brand": "Uniqlo",
            "size_label": "M",
            "overall_rating": "love",
            "measurements": measurements(),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return headers, created.json()["id"]


def signals_url(garment_id: str) -> str:
    return f"{GARMENTS}/{garment_id}/fit-signals"


# ---------------------------------------------------------------------------
# Creation.
# ---------------------------------------------------------------------------


async def test_creates_a_user_added_signal(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, garment_id = await authed_garment(client, db_session)

    response = await client.post(
        signals_url(garment_id),
        json={"dimension": "chest", "verdict": "slightly_tight"},
        headers=headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["owned_garment_id"] == garment_id
    assert body["dimension"] == "chest"
    assert body["verdict"] == "slightly_tight"
    # v1 manual entry is always user-added; the client cannot claim otherwise.
    assert body["source"] == "user_added"
    assert body["magnitude_cm"] is None
    assert body["use_case"] is None


async def test_source_is_not_settable_by_the_client(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """``source`` drives extraction-quality metrics, so a client must not be
    able to pass a signal off as NLP-extracted."""
    headers, garment_id = await authed_garment(client, db_session)

    response = await client.post(
        signals_url(garment_id),
        json={
            "dimension": "chest",
            "verdict": "preferred",
            "source": "nlp_extracted",
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["source"] == "user_added"


async def test_optional_fields_are_persisted(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, garment_id = await authed_garment(client, db_session)

    response = await client.post(
        signals_url(garment_id),
        json={
            "dimension": "sleeve_length",
            "verdict": "too_short",
            "magnitude_cm": 2.5,
            "use_case": "work",
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["magnitude_cm"] == 2.5
    assert response.json()["use_case"] == "work"

    row = (
        await db_session.execute(select(FitSignal).where(FitSignal.id == response.json()["id"]))
    ).scalar_one()
    # NUMERIC(6, 2) — the client's decimal value, not a binary-float artifact.
    assert row.magnitude_cm == Decimal("2.50")


@pytest.mark.parametrize("verdict", [v.value for v in Verdict])
async def test_every_prd_verdict_is_accepted(
    client: AsyncClient, db_session: AsyncSession, verdict: str
) -> None:
    """PRD §6.1's full verdict set, including the ``_short`` length-axis
    analogues, must round-trip."""
    headers, garment_id = await authed_garment(client, db_session)

    response = await client.post(
        signals_url(garment_id),
        json={"dimension": "sleeve_length", "verdict": verdict},
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["verdict"] == verdict


async def test_multiple_signals_can_attach_to_one_garment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, garment_id = await authed_garment(client, db_session)

    for dimension in ("chest", "neck_circumference", "sleeve_length"):
        response = await client.post(
            signals_url(garment_id),
            json={"dimension": dimension, "verdict": "preferred"},
            headers=headers,
        )
        assert response.status_code == 201

    rows = (
        (
            await db_session.execute(
                select(FitSignal).where(FitSignal.owned_garment_id == garment_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# raw_feedback_text is never null (CLAUDE.md).
# ---------------------------------------------------------------------------


async def test_raw_feedback_text_is_synthesized_when_absent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, garment_id = await authed_garment(client, db_session)

    response = await client.post(
        signals_url(garment_id),
        json={"dimension": "chest", "verdict": "slightly_tight"},
        headers=headers,
    )

    assert response.json()["raw_feedback_text"] == "User-added: chest slightly tight"


async def test_synthesized_text_includes_magnitude_and_use_case(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, garment_id = await authed_garment(client, db_session)

    response = await client.post(
        signals_url(garment_id),
        json={
            "dimension": "body_length",
            "verdict": "too_short",
            "magnitude_cm": 3.0,
            "use_case": "layering",
        },
        headers=headers,
    )

    assert response.json()["raw_feedback_text"] == (
        "User-added: body length too short (by 3cm, for layering)"
    )


async def test_supplied_feedback_text_is_kept_verbatim(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, garment_id = await authed_garment(client, db_session)
    words = "the collar chokes me but the body length is perfect"

    response = await client.post(
        signals_url(garment_id),
        json={
            "dimension": "neck_circumference",
            "verdict": "too_tight",
            "raw_feedback_text": words,
        },
        headers=headers,
    )

    assert response.json()["raw_feedback_text"] == words


async def test_empty_feedback_text_falls_back_to_the_synthesized_string(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An empty string would read as "the user said nothing", which is the
    confusion the non-null column exists to prevent."""
    headers, garment_id = await authed_garment(client, db_session)

    response = await client.post(
        signals_url(garment_id),
        json={"dimension": "chest", "verdict": "preferred", "raw_feedback_text": ""},
        headers=headers,
    )

    assert response.json()["raw_feedback_text"] == "User-added: chest preferred"


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", ["comfy", "TOO_TIGHT", "too tight", "", "perfect"])
async def test_unknown_verdict_is_422(
    client: AsyncClient, db_session: AsyncSession, verdict: str
) -> None:
    headers, garment_id = await authed_garment(client, db_session)

    response = await client.post(
        signals_url(garment_id),
        json={"dimension": "chest", "verdict": verdict},
        headers=headers,
    )

    assert response.status_code == 422
    assert ["body", "verdict"] in [error["loc"] for error in response.json()["detail"]]


@pytest.mark.parametrize("dimension", ["chset", "waist", "inseam", "chest_cm", "hem"])
async def test_dimension_outside_the_category_schema_is_422(
    client: AsyncClient, db_session: AsyncSession, dimension: str
) -> None:
    """Including ``chest_cm`` — the JSONB keys are canonical dimension names
    with no unit suffix, and a signal on a name the matching engine never
    reads would be silently inert."""
    headers, garment_id = await authed_garment(client, db_session)

    response = await client.post(
        signals_url(garment_id),
        json={"dimension": dimension, "verdict": "preferred"},
        headers=headers,
    )

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "dimension"]
    assert "chest" in error["msg"]


async def test_missing_required_fields_is_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, garment_id = await authed_garment(client, db_session)

    response = await client.post(signals_url(garment_id), json={}, headers=headers)

    assert response.status_code == 422
    locs = [error["loc"] for error in response.json()["detail"]]
    assert ["body", "dimension"] in locs
    assert ["body", "verdict"] in locs


async def test_negative_magnitude_is_422(client: AsyncClient, db_session: AsyncSession) -> None:
    """Direction lives in the verdict; magnitude is a distance."""
    headers, garment_id = await authed_garment(client, db_session)

    response = await client.post(
        signals_url(garment_id),
        json={"dimension": "chest", "verdict": "too_tight", "magnitude_cm": -1.0},
        headers=headers,
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Ownership and lifecycle.
# ---------------------------------------------------------------------------


async def test_requires_authentication(client: AsyncClient, db_session: AsyncSession) -> None:
    _, garment_id = await authed_garment(client, db_session)

    response = await client.post(
        signals_url(garment_id), json={"dimension": "chest", "verdict": "preferred"}
    )

    assert response.status_code == 401


async def test_cannot_add_a_signal_to_another_users_garment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice_headers, garment_id = await authed_garment(client, db_session)
    bob = await make_user(db_session, email="bob@example.com")
    bob_access, _ = await auth_jwt.issue_pair(bob.id, RefreshTokenRepository(db_session))

    hers = await client.post(
        signals_url(garment_id),
        json={"dimension": "chest", "verdict": "preferred"},
        headers={"Authorization": f"Bearer {bob_access}"},
    )
    ghost = await client.post(
        signals_url(str(uuid4())),
        json={"dimension": "chest", "verdict": "preferred"},
        headers={"Authorization": f"Bearer {bob_access}"},
    )

    assert hers.status_code == 404
    assert hers.json() == ghost.json()

    # Nothing was written to Alice's garment.
    rows = (
        (
            await db_session.execute(
                select(FitSignal).where(FitSignal.owned_garment_id == garment_id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []
    assert alice_headers  # the fixture's token is what created the garment


async def test_cannot_add_a_signal_to_a_deleted_garment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, garment_id = await authed_garment(client, db_session)
    assert (await client.delete(f"{GARMENTS}/{garment_id}", headers=headers)).status_code == 204

    response = await client.post(
        signals_url(garment_id),
        json={"dimension": "chest", "verdict": "preferred"},
        headers=headers,
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# The signal reaches the fit profile.
# ---------------------------------------------------------------------------


async def test_signal_reaches_the_fit_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A "slightly tight" chest signal must move the user's preferred chest
    measurement *up* — they want more room than this garment gave them.

    The snapshot is assembled from the persisted rows and handed to the
    real ``build_fit_profile``. ``GET /closet/fit-profile`` (TKT-P1-13)
    will do exactly this assembly; until it exists, this is where the
    ticket's "retrievable via fit-profile" criterion is proven.
    """
    headers, garment_id = await authed_garment(client, db_session)

    created = await client.post(
        signals_url(garment_id),
        json={"dimension": "chest", "verdict": "slightly_tight", "magnitude_cm": 2.0},
        headers=headers,
    )
    assert created.status_code == 201

    rows = (
        (
            await db_session.execute(
                select(FitSignal).where(FitSignal.owned_garment_id == garment_id)
            )
        )
        .scalars()
        .all()
    )
    assert [row.source for row in rows] == ["user_added"]

    profile = build_fit_profile(
        ClosetSnapshot(
            category_id=MENS_BUTTON_DOWN_SHIRT_ID,
            garments=[
                GarmentSnapshot(
                    label="Uniqlo M",
                    brand="Uniqlo",
                    size_label="M",
                    measurements_cm={"chest": 54.0},
                    stretch_level=StretchLevel.NONE,
                    overall_rating=OverallRating.LOVE,
                    signals=[
                        SignalSnapshot(
                            dimension=row.dimension,
                            verdict=Verdict(row.verdict),
                            magnitude_cm=float(row.magnitude_cm)
                            if row.magnitude_cm is not None
                            else None,
                            use_case=row.use_case,
                        )
                        for row in rows
                    ],
                )
            ],
        )
    )

    # The garment measured 54cm and ran slightly tight by 2cm, so the
    # preferred chest sits above the garment's own measurement.
    assert profile.dimensions["chest"].preferred_cm == pytest.approx(56.0)


@pytest.mark.parametrize("magnitude", [100000.0, 1e300])
async def test_out_of_range_magnitude_is_422_not_500(
    client: AsyncClient, db_session: AsyncSession, magnitude: float
) -> None:
    """``magnitude_cm`` lands in a NUMERIC(6, 2) column. Unbounded, an ordinary
    body reached Postgres and returned an unhandled NumericValueOutOfRangeError
    — a 500 where the contract says 422."""
    headers, garment_id = await authed_garment(client, db_session)

    response = await client.post(
        signals_url(garment_id),
        json={"dimension": "chest", "verdict": "too_tight", "magnitude_cm": magnitude},
        headers=headers,
    )

    assert response.status_code == 422
