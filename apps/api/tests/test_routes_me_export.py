"""Integration tests for ``GET /me/export`` — TKT-P1-17.

The ticket's acceptance criteria:

* a seeded user's export contains every row they own
* another user's data is not present

…plus the two ways an export can be quietly wrong: dropping rows past a
page boundary, and including shared catalog state that is not the user's
to take (PRD §11 data minimization).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from _factories import (
    make_brand_product,
    make_fit_signal,
    make_owned_garment,
    make_recommendation,
    make_user,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import jwt as auth_jwt
from api.config import get_settings
from api.models import GarmentCategory, User
from api.repositories.refresh_tokens import RefreshTokenRepository
from api.seeds.garment_categories import (
    MENS_BUTTON_DOWN_SHIRT_ID,
    seed_garment_categories,
)

EXPORT = "/me/export"
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


async def headers_for(session: AsyncSession, user: User) -> dict[str, str]:
    access, _ = await auth_jwt.issue_pair(user.id, RefreshTokenRepository(session))
    return {"Authorization": f"Bearer {access}"}


async def category(session: AsyncSession) -> GarmentCategory:
    row = await session.get(GarmentCategory, MENS_BUTTON_DOWN_SHIRT_ID)
    assert row is not None
    return row


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


# ---------------------------------------------------------------------------
# Shape and versioning.
# ---------------------------------------------------------------------------


async def test_export_of_an_empty_account_is_well_formed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, email="alice@example.com")

    response = await client.get(EXPORT, headers=await headers_for(db_session, user))

    assert response.status_code == 200
    body = response.json()
    assert body["export_schema_version"] == "1"
    assert body["exported_at"] is not None
    assert body["user"]["email"] == "alice@example.com"
    assert body["closet"] == []
    assert body["signals"] == []
    assert body["recommendations"] == []


async def test_export_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(EXPORT)).status_code == 401


async def test_profile_section_carries_the_consent_record(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The consent timestamp is personal data the controller relies on, so
    a user asking what we hold about them is entitled to see it."""
    user = await make_user(
        db_session,
        email="alice@example.com",
        privacy_consent_accepted_at=datetime(2026, 3, 4, 5, 6, tzinfo=UTC),
        stated_fit_preference="slim",
    )

    response = await client.get(EXPORT, headers=await headers_for(db_session, user))

    profile = response.json()["user"]
    assert profile["privacy_consent_accepted_at"] == "2026-03-04T05:06:00Z"
    assert profile["stated_fit_preference"] == "slim"
    assert profile["preferred_units"] == "cm"


# ---------------------------------------------------------------------------
# Completeness: every owned row is present.
# ---------------------------------------------------------------------------


async def test_export_contains_every_owned_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The ticket's headline criterion, across all four sections."""
    user = await make_user(db_session, email="alice@example.com")
    cat = await category(db_session)
    product = await make_brand_product(db_session, category=cat)

    shirt = await make_owned_garment(db_session, user=user, category=cat, brand="Uniqlo")
    jacket = await make_owned_garment(db_session, user=user, category=cat, brand="Everlane")
    signal_a = await make_fit_signal(db_session, owned_garment=shirt, dimension="chest")
    signal_b = await make_fit_signal(db_session, owned_garment=jacket, dimension="sleeve_length")
    recommendation = await make_recommendation(
        db_session, user=user, brand_product=product, recommended_size="L"
    )

    response = await client.get(EXPORT, headers=await headers_for(db_session, user))

    body = response.json()
    assert {item["id"] for item in body["closet"]} == {str(shirt.id), str(jacket.id)}
    assert {item["id"] for item in body["signals"]} == {str(signal_a.id), str(signal_b.id)}
    assert [item["id"] for item in body["recommendations"]] == [str(recommendation.id)]
    assert body["recommendations"][0]["recommended_size"] == "L"
    # ``prompt_version`` travels with the row so an exported recommendation
    # stays interpretable across prompt migrations (CLAUDE.md).
    assert body["recommendations"][0]["prompt_version"] == "manual-v0"


async def test_export_includes_soft_deleted_garments_marked_as_deleted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A tombstoned garment is still stored and still cited by historic
    recommendations, so it belongs in the export — flagged, not hidden."""
    user = await make_user(db_session)
    headers = await headers_for(db_session, user)
    created = await client.post(
        GARMENTS,
        json={
            "category_id": MENS_BUTTON_DOWN_SHIRT_ID,
            "brand": "Uniqlo",
            "size_label": "M",
            "measurements": measurements(),
        },
        headers=headers,
    )
    garment_id = created.json()["id"]
    assert (await client.delete(f"{GARMENTS}/{garment_id}", headers=headers)).status_code == 204

    body = (await client.get(EXPORT, headers=headers)).json()

    assert [item["id"] for item in body["closet"]] == [garment_id]
    assert body["closet"][0]["deleted_at"] is not None
    # …while the closet endpoint itself still hides it.
    assert (await client.get(GARMENTS, headers=headers)).json()["items"] == []


async def test_export_includes_signals_on_deleted_garments(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    cat = await category(db_session)
    garment = await make_owned_garment(
        db_session, user=user, category=cat, deleted_at=datetime(2026, 6, 1, tzinfo=UTC)
    )
    signal = await make_fit_signal(db_session, owned_garment=garment)

    body = (await client.get(EXPORT, headers=await headers_for(db_session, user))).json()

    assert [item["id"] for item in body["signals"]] == [str(signal.id)]


async def test_export_is_not_truncated_by_the_default_page_size(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The repositories page at 100 by default. An export that silently
    stopped there would look like a working endpoint and be a compliance
    failure, so it must fetch unbounded."""
    user = await make_user(db_session)
    cat = await category(db_session)
    for index in range(105):
        await make_owned_garment(db_session, user=user, category=cat, brand=f"Brand {index}")

    body = (await client.get(EXPORT, headers=await headers_for(db_session, user))).json()

    assert len(body["closet"]) == 105


# ---------------------------------------------------------------------------
# Isolation: nothing that isn't this user's.
# ---------------------------------------------------------------------------


async def test_another_users_data_is_absent(client: AsyncClient, db_session: AsyncSession) -> None:
    """The ticket's second criterion, checked across all three collections."""
    alice = await make_user(db_session, email="alice@example.com")
    bob = await make_user(db_session, email="bob@example.com")
    cat = await category(db_session)
    product = await make_brand_product(db_session, category=cat)

    bob_garment = await make_owned_garment(db_session, user=bob, category=cat, brand="Bob's")
    bob_signal = await make_fit_signal(db_session, owned_garment=bob_garment)
    bob_recommendation = await make_recommendation(db_session, user=bob, brand_product=product)
    await make_owned_garment(db_session, user=alice, category=cat, brand="Alice's")

    body = (await client.get(EXPORT, headers=await headers_for(db_session, alice))).json()

    assert body["user"]["email"] == "alice@example.com"
    assert [item["brand"] for item in body["closet"]] == ["Alice's"]

    serialized = str(body)
    for foreign_id in (bob.id, bob_garment.id, bob_signal.id, bob_recommendation.id):
        assert str(foreign_id) not in serialized
    assert "bob@example.com" not in serialized


async def test_export_is_always_the_token_holders_own(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """There is no parameter to point the export at another account — the
    subject comes from the bearer token."""
    alice = await make_user(db_session, email="alice@example.com")
    bob = await make_user(db_session, email="bob@example.com")

    response = await client.get(
        EXPORT,
        params={"user_id": str(bob.id), "email": "bob@example.com"},
        headers=await headers_for(db_session, alice),
    )

    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(alice.id)


# ---------------------------------------------------------------------------
# Minimization: no credentials, no shared catalog state (PRD §11).
# ---------------------------------------------------------------------------


async def test_export_never_contains_credentials(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, device_push_token="apns-token-abc123")
    await auth_jwt.issue_pair(user.id, RefreshTokenRepository(db_session))

    response = await client.get(EXPORT, headers=await headers_for(db_session, user))

    body = response.text
    assert "password_hash" not in body
    assert "$argon2" not in body
    assert "refresh" not in body
    assert "apns-token-abc123" not in body


async def test_export_omits_shared_catalog_state(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """``brand_product`` size charts and category schemas are shared state
    the user's rows merely reference — not personal data (PRD §11)."""
    user = await make_user(db_session)
    cat = await category(db_session)
    product = await make_brand_product(
        db_session,
        category=cat,
        product_name="Secret Catalog Oxford",
        size_chart={"M": {"chest_cm": 54, "body_length_cm": 71}},
    )
    await make_owned_garment(db_session, user=user, category=cat)
    await make_recommendation(db_session, user=user, brand_product=product)

    body = (await client.get(EXPORT, headers=await headers_for(db_session, user))).json()

    serialized = str(body)
    assert "Secret Catalog Oxford" not in serialized
    assert "size_chart" not in serialized
    assert "measurement_schema" not in serialized
    assert "dimension_weights" not in serialized
    # The reference itself survives, so the recommendation stays identifiable.
    assert body["recommendations"][0]["brand_product_id"] == str(product.id)


# ---------------------------------------------------------------------------
# Round-trip fidelity.
# ---------------------------------------------------------------------------


async def test_exported_garment_matches_what_the_closet_endpoint_returned(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """ "A user importing their export elsewhere sees the same JSON they saw
    in-app" — the export shape is the read shape plus ``deleted_at``."""
    user = await make_user(db_session)
    headers = await headers_for(db_session, user)
    created = await client.post(
        GARMENTS,
        json={
            "category_id": MENS_BUTTON_DOWN_SHIRT_ID,
            "brand": "Uniqlo",
            "product_name": "Oxford",
            "size_label": "M",
            "measurements": measurements(),
            "use_cases": ["work"],
        },
        headers=headers,
    )
    assert created.status_code == 201

    exported = (await client.get(EXPORT, headers=headers)).json()["closet"][0]

    assert exported == {**created.json(), "deleted_at": None}


async def test_exported_recommendation_keeps_full_precision_confidence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """``confidence`` is ``NUMERIC(4, 3)``; the export must not round it to
    a float on the way out."""
    user = await make_user(db_session)
    cat = await category(db_session)
    product = await make_brand_product(db_session, category=cat)
    await make_recommendation(
        db_session, user=user, brand_product=product, confidence=Decimal("0.615")
    )

    body = (await client.get(EXPORT, headers=await headers_for(db_session, user))).json()

    assert Decimal(body["recommendations"][0]["confidence"]) == Decimal("0.615")
