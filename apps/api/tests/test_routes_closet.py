"""Integration tests for ``/closet/garments`` — TKT-P1-09.

The ticket's acceptance criteria:

* create → list → patch → delete
* ownership isolation (user A cannot read user B's garments)
* out-of-range measurement → 422 with a per-field error

…plus the soft-delete guarantee the recommendation history depends on,
and the v1 category gate.

Every test authenticates for real: a signed access token in an
``Authorization`` header, resolved by ``deps.CurrentUser``. Nothing here
overrides the auth dependency, so a regression in TKT-P1-08 surfaces as a
closet failure too.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from _factories import make_owned_garment, make_user
from _keys import TEST_JWT_SECRET
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import jwt as auth_jwt
from api.config import get_settings
from api.deps import get_session
from api.main import create_app
from api.models import GarmentCategory, OwnedGarment
from api.repositories import GarmentCategoryRepository
from api.repositories.refresh_tokens import RefreshTokenRepository
from api.seeds.garment_categories import (
    MENS_BUTTON_DOWN_SHIRT_ID,
    seed_garment_categories,
)

GARMENTS = "/closet/garments"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    get_settings.cache_clear()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _seeded_category(db_session: AsyncSession) -> AsyncIterator[None]:
    """The button-down category must exist: it owns the measurement schema
    every write validates against. Seeded per test inside the rolled-back
    transaction rather than assumed present in the dev DB."""
    await seed_garment_categories(db_session)
    await db_session.flush()
    yield


def measurement(value: float) -> dict[str, Any]:
    return {"value": value, "unit": "cm", "source": "manual_tape"}


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
    return {name: measurement(value) for name, value in full.items()}


def garment_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "category_id": MENS_BUTTON_DOWN_SHIRT_ID,
        "brand": "Uniqlo",
        "product_name": "Oxford Slim Fit",
        "size_label": "M",
        "measurements": measurements(),
        "fabric_composition": "100% cotton",
        "stretch_level": "none",
        "overall_rating": "love",
        "use_cases": ["work"],
    }
    body.update(overrides)
    return body


async def auth_headers(session: AsyncSession, **user_overrides: Any) -> dict[str, str]:
    """Create a user and return a usable ``Authorization`` header for them."""
    user = await make_user(session, **user_overrides)
    access, _ = await auth_jwt.issue_pair(user.id, RefreshTokenRepository(session))
    return {"Authorization": f"Bearer {access}"}


# ---------------------------------------------------------------------------
# The ticket's headline round trip.
# ---------------------------------------------------------------------------


async def test_create_list_patch_delete_round_trip(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await auth_headers(db_session)

    created = await client.post(GARMENTS, json=garment_body(), headers=headers)
    assert created.status_code == 201, created.text
    garment = created.json()
    assert garment["brand"] == "Uniqlo"
    assert garment["measurements"]["chest"] == measurement(54.0)
    assert garment["category_id"] == MENS_BUTTON_DOWN_SHIRT_ID

    listed = await client.get(GARMENTS, headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [garment["id"]]

    patched = await client.patch(
        f"{GARMENTS}/{garment['id']}",
        json={"size_label": "L", "overall_rating": "like"},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["size_label"] == "L"
    assert patched.json()["overall_rating"] == "like"
    # Untouched fields survive.
    assert patched.json()["brand"] == "Uniqlo"
    assert patched.json()["measurements"] == garment["measurements"]

    deleted = await client.delete(f"{GARMENTS}/{garment['id']}", headers=headers)
    assert deleted.status_code == 204

    after = await client.get(GARMENTS, headers=headers)
    assert after.json()["items"] == []


async def test_list_is_ordered_oldest_first(client: AsyncClient, db_session: AsyncSession) -> None:
    """Written out of order, listed in ``created_at`` order.

    The rows are inserted directly with explicit timestamps: Postgres
    ``now()`` is transaction-start time, so garments created through the API
    inside one test transaction all share a ``created_at`` and could not
    distinguish real ordering from insertion sequence.
    """
    user = await make_user(db_session)
    access, _ = await auth_jwt.issue_pair(user.id, RefreshTokenRepository(db_session))
    headers = {"Authorization": f"Bearer {access}"}
    category = await db_session.get(GarmentCategory, MENS_BUTTON_DOWN_SHIRT_ID)

    for brand, day in (("Third", 3), ("First", 1), ("Second", 2)):
        await make_owned_garment(
            db_session,
            user=user,
            category=category,
            brand=brand,
            created_at=datetime(2026, 1, day, tzinfo=UTC),
        )

    listed = await client.get(GARMENTS, headers=headers)

    assert [item["brand"] for item in listed.json()["items"]] == ["First", "Second", "Third"]


async def test_list_order_is_stable_across_calls(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """``created_at`` ties break on ``id``, so a client polling its closet
    never sees the same garments shuffle between requests."""
    headers = await auth_headers(db_session)
    for brand in ("A", "B", "C", "D", "E"):
        assert (
            await client.post(GARMENTS, json=garment_body(brand=brand), headers=headers)
        ).status_code == 201

    first = await client.get(GARMENTS, headers=headers)
    second = await client.get(GARMENTS, headers=headers)

    assert len(first.json()["items"]) == 5
    assert [item["id"] for item in first.json()["items"]] == [
        item["id"] for item in second.json()["items"]
    ]


async def test_empty_closet_lists_nothing(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = await auth_headers(db_session)

    response = await client.get(GARMENTS, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"items": []}


# ---------------------------------------------------------------------------
# Ownership isolation.
# ---------------------------------------------------------------------------


async def test_user_a_cannot_see_user_b_garments_in_the_list(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice = await auth_headers(db_session, email="alice@example.com")
    bob = await auth_headers(db_session, email="bob@example.com")
    await client.post(GARMENTS, json=garment_body(brand="Alice's"), headers=alice)

    listed = await client.get(GARMENTS, headers=bob)

    assert listed.json()["items"] == []


@pytest.mark.parametrize("method", ["patch", "delete"])
async def test_user_a_cannot_mutate_user_b_garments(
    client: AsyncClient, db_session: AsyncSession, method: str
) -> None:
    """Another user's garment is a 404 — the same response as a garment id
    that never existed, so the endpoint reveals nothing about what Bob owns."""
    alice = await auth_headers(db_session, email="alice@example.com")
    bob = await auth_headers(db_session, email="bob@example.com")
    created = await client.post(GARMENTS, json=garment_body(), headers=alice)
    garment_id = created.json()["id"]

    call = getattr(client, method)
    kwargs: dict[str, Any] = {"headers": bob}
    if method == "patch":
        kwargs["json"] = {"brand": "Hijacked"}
    hers = await call(f"{GARMENTS}/{garment_id}", **kwargs)
    ghost = await call(f"{GARMENTS}/{uuid4()}", **kwargs)

    assert hers.status_code == 404
    assert hers.json() == ghost.json()

    # And Alice's garment is untouched.
    still = await client.get(GARMENTS, headers=alice)
    assert len(still.json()["items"]) == 1
    assert still.json()["items"][0]["brand"] == "Uniqlo"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", GARMENTS, None),
        ("post", GARMENTS, {}),
        ("patch", f"{GARMENTS}/{uuid4()}", {}),
        ("delete", f"{GARMENTS}/{uuid4()}", None),
    ],
)
async def test_every_endpoint_requires_authentication(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    kwargs: dict[str, Any] = {} if body is None else {"json": body}

    response = await getattr(client, method)(path, **kwargs)

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Measurement validation (PRD §5.2).
# ---------------------------------------------------------------------------


async def test_out_of_range_measurement_is_422_with_a_per_field_error(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """PRD §5.2: a chest measurement over 80cm is out of range."""
    headers = await auth_headers(db_session)

    response = await client.post(
        GARMENTS, json=garment_body(measurements=measurements(chest=95.0)), headers=headers
    )

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert [error["loc"] for error in errors] == [["body", "measurements", "chest", "value"]]
    assert "95.0cm" in errors[0]["msg"]
    assert "80.0cm" in errors[0]["msg"]


async def test_missing_required_measurement_is_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await auth_headers(db_session)
    partial = measurements()
    del partial["neck_circumference"]

    response = await client.post(GARMENTS, json=garment_body(measurements=partial), headers=headers)

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [
        ["body", "measurements", "neck_circumference"]
    ]


async def test_every_measurement_error_is_reported_at_once(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await auth_headers(db_session)

    response = await client.post(GARMENTS, json=garment_body(measurements={}), headers=headers)

    assert response.status_code == 422
    assert len(response.json()["detail"]) == 6


async def test_unknown_measurement_dimension_is_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await auth_headers(db_session)
    typo = measurements()
    typo["chset"] = typo.pop("chest")

    response = await client.post(GARMENTS, json=garment_body(measurements=typo), headers=headers)

    assert response.status_code == 422
    locs = [error["loc"] for error in response.json()["detail"]]
    assert ["body", "measurements", "chset"] in locs


async def test_non_cm_unit_is_rejected_by_the_schema_layer(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Measurements are cm-only internally (CLAUDE.md); conversion belongs
    at the UI boundary, so the wire format never carries inches."""
    headers = await auth_headers(db_session)
    inches = measurements()
    inches["chest"] = {"value": 21.0, "unit": "in", "source": "manual_tape"}

    response = await client.post(GARMENTS, json=garment_body(measurements=inches), headers=headers)

    assert response.status_code == 422


async def test_patch_revalidates_measurements(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await auth_headers(db_session)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)
    garment_id = created.json()["id"]

    response = await client.patch(
        f"{GARMENTS}/{garment_id}",
        json={"measurements": measurements(chest=200.0)},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "measurements", "chest", "value"]


async def test_patch_replaces_the_whole_measurement_set(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Partial merge is not offered: a client sends the full set it wants
    stored, so it always knows exactly what it saved."""
    headers = await auth_headers(db_session)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)

    response = await client.patch(
        f"{created.json()['id']}".join([f"{GARMENTS}/", ""]),
        json={"measurements": measurements(chest=56.0)},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["measurements"]["chest"] == measurement(56.0)
    assert response.json()["measurements"]["body_length"] == measurement(71.0)


async def test_patch_with_an_incomplete_measurement_set_is_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await auth_headers(db_session)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)
    partial = {"chest": measurement(54.0)}

    response = await client.patch(
        f"{GARMENTS}/{created.json()['id']}", json={"measurements": partial}, headers=headers
    )

    assert response.status_code == 422
    assert len(response.json()["detail"]) == 5


# ---------------------------------------------------------------------------
# v1 category scope (CLAUDE.md).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category_id",
    ["womens_blouse", "mens_trousers", "button_down_shirt", "unknown"],
)
async def test_out_of_scope_category_is_422(
    client: AsyncClient, db_session: AsyncSession, category_id: str
) -> None:
    headers = await auth_headers(db_session)

    response = await client.post(
        GARMENTS, json=garment_body(category_id=category_id), headers=headers
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "category_id"]
    assert MENS_BUTTON_DOWN_SHIRT_ID in response.json()["detail"][0]["msg"]


async def test_category_cannot_be_changed_by_patch(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Re-categorizing would strand the garment's fit signals on dimensions
    the new category may not declare, so the field is not updatable at all."""
    headers = await auth_headers(db_session)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)

    response = await client.patch(
        f"{GARMENTS}/{created.json()['id']}",
        json={"category_id": "womens_blouse"},
        headers=headers,
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Soft delete.
# ---------------------------------------------------------------------------


async def test_delete_tombstones_the_row_rather_than_removing_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Historic recommendations cite closet rows by id (PRD §5.4 reference
    garments), so the row must outlive its removal from the closet."""
    headers = await auth_headers(db_session)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)
    garment_id = UUID(created.json()["id"])

    assert (await client.delete(f"{GARMENTS}/{garment_id}", headers=headers)).status_code == 204

    row = (
        await db_session.execute(select(OwnedGarment).where(OwnedGarment.id == garment_id))
    ).scalar_one()
    assert row.deleted_at is not None
    assert row.brand == "Uniqlo"  # the data itself is intact


async def test_deleted_garment_is_invisible_to_every_read(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await auth_headers(db_session)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)
    garment_id = created.json()["id"]
    await client.delete(f"{GARMENTS}/{garment_id}", headers=headers)

    listed = await client.get(GARMENTS, headers=headers)
    patched = await client.patch(
        f"{GARMENTS}/{garment_id}", json={"brand": "Zombie"}, headers=headers
    )

    assert listed.json()["items"] == []
    assert patched.status_code == 404


async def test_deleting_twice_is_404_and_does_not_move_the_tombstone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """When a garment left the closet is a fact; a retry must not rewrite it."""
    headers = await auth_headers(db_session)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)
    garment_id = UUID(created.json()["id"])
    await client.delete(f"{GARMENTS}/{garment_id}", headers=headers)

    row = (
        await db_session.execute(select(OwnedGarment).where(OwnedGarment.id == garment_id))
    ).scalar_one()
    first_deleted_at = row.deleted_at

    second = await client.delete(f"{GARMENTS}/{garment_id}", headers=headers)

    assert second.status_code == 404
    await db_session.refresh(row)
    assert row.deleted_at == first_deleted_at


# ---------------------------------------------------------------------------
# Persistence details.
# ---------------------------------------------------------------------------


async def test_measurement_provenance_survives_a_round_trip(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """PRD §A.9: the CV-assist flow needs per-measurement provenance, so
    ``source`` must be stored, not dropped on the way in."""
    headers = await auth_headers(db_session)
    mixed = measurements()
    mixed["chest"] = {"value": 54.0, "unit": "cm", "source": "cv_assisted"}

    created = await client.post(GARMENTS, json=garment_body(measurements=mixed), headers=headers)
    assert created.status_code == 201

    listed = await client.get(GARMENTS, headers=headers)
    stored = listed.json()["items"][0]["measurements"]
    assert stored["chest"]["source"] == "cv_assisted"
    assert stored["body_length"]["source"] == "manual_tape"


async def test_optional_fields_default_and_can_be_cleared(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await auth_headers(db_session)
    minimal = {
        "category_id": MENS_BUTTON_DOWN_SHIRT_ID,
        "brand": "No Frills",
        "size_label": "M",
        "measurements": measurements(),
    }

    created = await client.post(GARMENTS, json=minimal, headers=headers)
    assert created.status_code == 201
    assert created.json()["product_name"] is None
    assert created.json()["use_cases"] == []

    patched = await client.patch(
        f"{GARMENTS}/{created.json()['id']}",
        json={"product_name": "Now Named", "use_cases": ["work", "casual"]},
        headers=headers,
    )
    assert patched.json()["product_name"] == "Now Named"
    assert patched.json()["use_cases"] == ["work", "casual"]

    cleared = await client.patch(
        f"{GARMENTS}/{created.json()['id']}", json={"product_name": None}, headers=headers
    )
    assert cleared.json()["product_name"] is None


async def test_empty_patch_is_a_no_op_returning_the_garment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await auth_headers(db_session)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)

    response = await client.patch(f"{GARMENTS}/{created.json()['id']}", json={}, headers=headers)

    assert response.status_code == 200
    assert response.json() == created.json()


async def test_unknown_patch_field_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """``OwnedGarmentUpdate`` forbids extras, so a client typo fails loudly
    instead of silently not applying."""
    headers = await auth_headers(db_session)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)

    response = await client.patch(
        f"{GARMENTS}/{created.json()['id']}", json={"brnad": "Typo"}, headers=headers
    )

    assert response.status_code == 422


async def test_garment_is_owned_by_the_authenticated_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """``user_id`` comes from the token, never from the body — a client
    cannot plant a garment in someone else's closet.

    The body field is now rejected rather than ignored, so a client that
    thought it was choosing an owner is told it was not.
    """
    alice = await make_user(db_session, email="alice@example.com")
    bob = await make_user(db_session, email="bob@example.com")
    access, _ = await auth_jwt.issue_pair(alice.id, RefreshTokenRepository(db_session))
    headers = {"Authorization": f"Bearer {access}"}

    spoofed = await client.post(GARMENTS, json=garment_body(user_id=str(bob.id)), headers=headers)
    honest = await client.post(GARMENTS, json=garment_body(), headers=headers)

    assert spoofed.status_code == 422
    # And the ordinary path still attributes the garment to the token holder.
    assert honest.status_code == 201
    assert honest.json()["user_id"] == str(alice.id)


async def test_closet_list_is_not_silently_truncated(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The repository pages at 100 by default. Inheriting that made this
    endpoint disagree with the export and the profile builder, both of which
    read unbounded: garment 101 onward shaped the user's recommendations
    while being invisible in their closet and impossible to edit or delete.
    """
    user = await make_user(db_session)
    access, _ = await auth_jwt.issue_pair(user.id, RefreshTokenRepository(db_session))
    headers = {"Authorization": f"Bearer {access}"}
    category = await db_session.get(GarmentCategory, MENS_BUTTON_DOWN_SHIRT_ID)
    for index in range(105):
        await make_owned_garment(db_session, user=user, category=category, brand=f"Brand {index}")

    listed = await client.get(GARMENTS, headers=headers)

    assert len(listed.json()["items"]) == 105


@pytest.mark.parametrize("field", ["brand", "size_label", "measurements", "use_cases"])
async def test_patch_cannot_clear_a_not_null_column(
    client: AsyncClient, db_session: AsyncSession, field: str
) -> None:
    """``{"measurements": null}`` used to store ``'null'::jsonb`` and brick the
    account permanently: SQLAlchemy maps Python ``None`` onto JSON null rather
    than SQL NULL, so the NOT NULL constraint never fired and the closet list,
    the fit profile and the GDPR export all raised from then on."""
    headers = await auth_headers(db_session)
    created = await client.post(GARMENTS, json=garment_body(), headers=headers)

    response = await client.patch(
        f"{GARMENTS}/{created.json()['id']}", json={field: None}, headers=headers
    )

    assert response.status_code == 422
    # And the garment is still readable.
    assert (await client.get(GARMENTS, headers=headers)).status_code == 200


async def test_create_rejects_unknown_fields(client: AsyncClient, db_session: AsyncSession) -> None:
    """Create and update must agree about strictness. Create used to drop
    unknown fields silently while PATCH rejected them, so the same typo
    failed loudly on one verb and silently on the other."""
    headers = await auth_headers(db_session)

    response = await client.post(GARMENTS, json=garment_body(brnad="typo"), headers=headers)

    assert response.status_code == 422
    assert ["body", "brnad"] in [error["loc"] for error in response.json()["detail"]]


async def test_closet_size_is_capped(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST was an unbounded row-creation primitive for anyone holding a valid
    token. The real ceiling is far above any closet (PRD §10.1 asks for 3-5 at
    onboarding); lowered here so the test does not write 500 rows."""
    monkeypatch.setenv("MAX_CLOSET_GARMENTS", "2")
    get_settings.cache_clear()
    headers = await auth_headers(db_session)

    for index in range(2):
        created = await client.post(
            GARMENTS, json=garment_body(brand=f"Brand {index}"), headers=headers
        )
        assert created.status_code == 201

    overflow = await client.post(GARMENTS, json=garment_body(brand="One too many"), headers=headers)

    assert overflow.status_code == 409
    assert "full" in overflow.json()["detail"].lower()
    # A delete makes room again.
    listed = await client.get(GARMENTS, headers=headers)
    assert (
        await client.delete(f"{GARMENTS}/{listed.json()['items'][0]['id']}", headers=headers)
    ).status_code == 204
    assert (
        await client.post(GARMENTS, json=garment_body(brand="Now fits"), headers=headers)
    ).status_code == 201


async def test_unseeded_category_is_a_500_logged_for_the_operator(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The category row owns the measurement schema every write validates
    against, so a database that never ran the seed cannot answer this request
    at all.

    That is the server being misconfigured, not the client sending something
    wrong — hence 500 rather than the 422 the category gate returns. The
    remediation belongs in the log, though, not the response: the body used to
    name the package path and the toolchain (``uv run python -m api.seeds…``),
    which any authenticated client could elicit from an unseeded environment.
    """
    headers = await auth_headers(db_session)

    # Deleting the row is not an option — brand_product references it — so the
    # lookup is blinded instead, which is what an unseeded database looks like
    # from the handler's side.
    async def _unseeded(self: GarmentCategoryRepository, id: str) -> GarmentCategory | None:
        return None

    monkeypatch.setattr(GarmentCategoryRepository, "get", _unseeded)

    with caplog.at_level(logging.ERROR, logger="api.routes.closet"):
        response = await client.post(GARMENTS, json=garment_body(), headers=headers)

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "uv run" not in detail and "api.seeds" not in detail, detail
    # The operator still gets what they need, with the category that is missing.
    assert any(
        record.message == "closet.category.unseeded"
        and getattr(record, "category_id", None) == MENS_BUTTON_DOWN_SHIRT_ID
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# The authenticated-surface rate limiter, end to end.
#
# The middleware over ``/closet/*`` and ``/me`` had no HTTP-level test at all:
# the unit tests built a middleware with ``key_by_bearer=False`` and then set
# the private attribute by hand, so they exercised ``_principal`` without ever
# exercising the wiring. Deleting the whole second ``add_middleware`` block
# from ``create_app`` left the suite green, which is how a limiter that could
# be bypassed by rotating the token shipped unnoticed.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def user_throttled_client(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    """Client whose app was built with a 2-request authenticated budget.

    Built here rather than via the shared ``app`` fixture for the same reason
    as ``throttled_client`` in ``test_routes_auth``: the limiter is
    constructed inside ``create_app``, so the env var must be set first.
    """
    monkeypatch.setenv("USER_RATE_LIMIT_CAPACITY", "2")
    monkeypatch.setenv("USER_RATE_LIMIT_WINDOW_SECONDS", "60")
    get_settings.cache_clear()
    app = create_app()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as throttled:
            yield throttled
    finally:
        app.dependency_overrides.clear()


async def test_closet_reads_are_rate_limited(
    user_throttled_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The authenticated surface throttles at all — nothing asserted this."""
    headers = await auth_headers(db_session)

    first = await user_throttled_client.get(GARMENTS, headers=headers)
    second = await user_throttled_client.get(GARMENTS, headers=headers)
    third = await user_throttled_client.get(GARMENTS, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    # capacity 2 over a 60s window refills one token every 30s.
    assert third.headers["retry-after"] == "30"


async def test_the_me_prefix_is_throttled_too(
    user_throttled_client: AsyncClient, db_session: AsyncSession
) -> None:
    """``/me`` is registered as a bare prefix, not ``/me/`` — easy to get wrong,
    and the GDPR export is the most expensive thing behind it."""
    headers = await auth_headers(db_session)

    for _ in range(2):
        await user_throttled_client.get("/me/export", headers=headers)
    third = await user_throttled_client.get("/me/export", headers=headers)

    assert third.status_code == 429


async def test_rotating_the_bearer_token_does_not_escape_the_budget(
    user_throttled_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The bypass. This is the test whose absence let it ship.

    The limiter used to bucket on a SHA-256 of the raw Authorization header,
    so the *caller* chose the bucket: a fresh token string meant a fresh
    bucket, starting at full capacity. No account was needed, because the
    middleware runs before routing and the 401 arrives too late to matter.
    Measured against the pre-fix code, 50 requests from one address with a
    rotating forged token were throttled 0 times.
    """
    statuses = [
        (
            await user_throttled_client.get(
                GARMENTS, headers={"Authorization": f"Bearer forged-{i}"}
            )
        ).status_code
        for i in range(10)
    ]

    assert 429 in statuses, "rotating the bearer value bypassed the limiter"


async def test_two_users_on_one_connection_do_not_share_a_budget(
    user_throttled_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The reason for keying on the credential rather than the address: two
    users behind one NAT must not exhaust each other."""
    alice = await auth_headers(db_session, email="alice-throttle@example.com")
    bob = await auth_headers(db_session, email="bob-throttle@example.com")

    for _ in range(3):
        await user_throttled_client.get(GARMENTS, headers=alice)
    bobs_turn = await user_throttled_client.get(GARMENTS, headers=bob)

    assert bobs_turn.status_code == 200, "Alice's flood locked Bob out"


async def test_parameterized_routes_get_their_own_bucket(
    user_throttled_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Exhausting the list endpoint must not also block a delete.

    ``known_paths`` holds route *templates*, so comparing them against the
    concrete request path meant every parameterized route collapsed into the
    shared ``<unrouted>`` bucket together.
    """
    headers = await auth_headers(db_session)
    created = await user_throttled_client.post(GARMENTS, json=garment_body(), headers=headers)
    assert created.status_code == 201
    garment_id = created.json()["id"]

    for _ in range(3):
        await user_throttled_client.get(GARMENTS, headers=headers)

    deleted = await user_throttled_client.delete(f"{GARMENTS}/{garment_id}", headers=headers)

    assert deleted.status_code == 204, "the list bucket swallowed the delete route"


async def test_both_validation_layers_use_one_error_vocabulary(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """One endpoint, one set of machine-readable error codes.

    Two layers reject a body here: Pydantic, for what is static (a blank
    brand), and the domain, for what the category schema declares (a chest
    measurement outside PRD §5.2's range). ``type`` is the only field a client
    can branch on, and it used to come back in two vocabularies — Pydantic's
    v2 snake_case from one layer, hand-written v1-style dotted strings
    (``value_error.measurement.below_minimum``) from the other.

    Asserts the shape they now share rather than the exact spellings, so
    adding a new domain check does not need this test edited — only a new
    check that reaches for a dot does.
    """
    headers = await auth_headers(db_session)

    pydantic_rejected = await client.post(GARMENTS, json=garment_body(brand=""), headers=headers)
    domain_rejected = await client.post(
        GARMENTS,
        json=garment_body(
            measurements={
                **measurements(),
                "chest": {"value": 5.0, "unit": "cm", "source": "manual_tape"},
            }
        ),
        headers=headers,
    )

    assert pydantic_rejected.status_code == 422
    assert domain_rejected.status_code == 422

    codes = [
        error["type"]
        for response in (pydantic_rejected, domain_rejected)
        for error in response.json()["detail"]
    ]
    assert codes, "neither layer produced an error entry"
    assert all("." not in code for code in codes), f"mixed code vocabularies: {codes}"
    assert all(code == code.lower() for code in codes), codes

    # And every entry still names where it came from, from either layer.
    for response in (pydantic_rejected, domain_rejected):
        for error in response.json()["detail"]:
            assert error["loc"][0] == "body"
            assert error["msg"]
