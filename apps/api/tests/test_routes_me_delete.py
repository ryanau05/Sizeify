"""Integration tests for ``DELETE /me`` — TKT-P1-18.

The ticket's acceptance criteria:

* after deletion, calls with the old tokens return 401
* row counts for that user are zero across every table

…plus a direct check that the ``ON DELETE CASCADE`` chain the route relies
on is actually declared, so a table added later without it fails here
rather than silently orphaning rows.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
import sqlalchemy as sa
from _api import signup_and_login
from _factories import (
    TEST_PASSWORD,
    make_brand_product,
    make_fit_signal,
    make_owned_garment,
    make_recommendation,
    make_user,
)
from _keys import TEST_JWT_SECRET
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import jwt as auth_jwt
from api.config import get_settings
from api.models import (
    BrandProduct,
    FitSignal,
    GarmentCategory,
    OwnedGarment,
    Recommendation,
    RefreshToken,
    User,
)
from api.repositories.refresh_tokens import RefreshTokenRepository
from api.seeds.garment_categories import (
    MENS_BUTTON_DOWN_SHIRT_ID,
    seed_garment_categories,
)

ME = "/me"
#: DELETE /me now re-authenticates (PRD §11), so every call carries the password.
CONFIRM = {"password": TEST_PASSWORD}
SIGNUP_CONFIRM = {"password": "Str0ng-Passphrase"}


async def erase(client: AsyncClient, access: str, body: dict[str, str] | None = None) -> Any:
    """``DELETE /me`` with its confirmation body.

    httpx's convenience ``client.delete()`` takes no body, so this goes
    through ``client.request``. Worth knowing when writing the mobile
    clients: DELETE-with-a-body is legal and FastAPI serves it, but not
    every HTTP library exposes it on the convenience method.
    """
    return await client.request(
        "DELETE",
        ME,
        headers={"Authorization": f"Bearer {access}"},
        json=CONFIRM if body is None else body,
    )


EXPORT = "/me/export"
GARMENTS = "/closet/garments"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    get_settings.cache_clear()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _seeded_category(db_session: AsyncSession) -> AsyncIterator[None]:
    await seed_garment_categories(db_session)
    await db_session.flush()
    yield


async def category(session: AsyncSession) -> GarmentCategory:
    row = await session.get(GarmentCategory, MENS_BUTTON_DOWN_SHIRT_ID)
    assert row is not None
    return row


async def populated_user(session: AsyncSession, **overrides: Any) -> tuple[User, dict[str, UUID]]:
    """A user with one row in every table that hangs off them."""
    user = await make_user(session, **overrides)
    cat = await category(session)
    product = await make_brand_product(session, category=cat)
    garment = await make_owned_garment(session, user=user, category=cat)
    signal = await make_fit_signal(session, owned_garment=garment)
    recommendation = await make_recommendation(session, user=user, brand_product=product)
    return user, {
        "garment": garment.id,
        "signal": signal.id,
        "recommendation": recommendation.id,
        "brand_product": product.id,
    }


async def token_pair(session: AsyncSession, user: User) -> tuple[str, str]:
    return await auth_jwt.issue_pair(user.id, RefreshTokenRepository(session))


async def count_for_user(session: AsyncSession, user_id: UUID) -> dict[str, int]:
    """Row counts across every table that can hold this user's data."""

    async def count(stmt: sa.Select[Any]) -> int:
        return int((await session.execute(stmt)).scalar_one())

    return {
        "user": await count(sa.select(sa.func.count()).select_from(User).where(User.id == user_id)),
        "refresh_token": await count(
            sa.select(sa.func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == user_id)
        ),
        "owned_garment": await count(
            sa.select(sa.func.count())
            .select_from(OwnedGarment)
            .where(OwnedGarment.user_id == user_id)
        ),
        "fit_signal": await count(
            sa.select(sa.func.count())
            .select_from(FitSignal)
            .join(OwnedGarment, OwnedGarment.id == FitSignal.owned_garment_id)
            .where(OwnedGarment.user_id == user_id)
        ),
        "recommendation": await count(
            sa.select(sa.func.count())
            .select_from(Recommendation)
            .where(Recommendation.user_id == user_id)
        ),
    }


# ---------------------------------------------------------------------------
# The ticket's acceptance criteria.
# ---------------------------------------------------------------------------


async def test_delete_removes_every_row_for_that_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user, _ = await populated_user(db_session)
    access, _ = await token_pair(db_session, user)

    before = await count_for_user(db_session, user.id)
    assert all(count == 1 for count in before.values()), before

    response = await erase(client, access)

    assert response.status_code == 204
    assert await count_for_user(db_session, user.id) == dict.fromkeys(before, 0)


async def test_old_access_token_stops_working(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user, _ = await populated_user(db_session)
    access, _ = await token_pair(db_session, user)
    headers = {"Authorization": f"Bearer {access}"}

    assert (await client.request("DELETE", ME, headers=headers, json=CONFIRM)).status_code == 204

    # The token is still unexpired and correctly signed — it fails because
    # the subject it names no longer exists.
    assert (await client.get(EXPORT, headers=headers)).status_code == 401
    assert (await client.get(GARMENTS, headers=headers)).status_code == 401
    assert (await client.request("DELETE", ME, headers=headers, json=CONFIRM)).status_code == 401


async def test_old_refresh_token_cannot_be_rotated_back_into_access(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The token chain is deleted with the account, so a stale refresh
    cannot mint a working credential for a user that no longer exists."""
    user, _ = await populated_user(db_session)
    access, refresh = await token_pair(db_session, user)

    assert (await erase(client, access)).status_code == 204

    response = await client.post("/auth/refresh", json={"refresh_token": refresh})

    assert response.status_code == 401


async def test_deleted_account_cannot_log_back_in(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access = await signup_and_login(client)

    assert (await erase(client, access, SIGNUP_CONFIRM)).status_code == 204

    login = await client.post(
        "/auth/login", json={"email": "alice@example.com", "password": "Str0ng-Passphrase"}
    )

    assert login.status_code == 401


async def test_the_email_becomes_reusable(client: AsyncClient, db_session: AsyncSession) -> None:
    """Erasure means erasure: the address is not reserved by a tombstone,
    so the same person can sign up again as a genuinely new account."""
    first_access = await signup_and_login(client)
    await erase(client, first_access, SIGNUP_CONFIRM)

    second_access = await signup_and_login(client)

    assert auth_jwt.decode(second_access).user_id != auth_jwt.decode(first_access).user_id


# ---------------------------------------------------------------------------
# Blast radius: nobody else's data, and no shared catalog state.
# ---------------------------------------------------------------------------


async def test_another_users_data_is_untouched(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, _ = await populated_user(db_session, email="alice@example.com")
    bob, _ = await populated_user(db_session, email="bob@example.com")
    access, _ = await token_pair(db_session, alice)
    # Bob gets a token chain too, so every counted table holds a row of his.
    await token_pair(db_session, bob)

    assert (await erase(client, access)).status_code == 204

    alice_counts = await count_for_user(db_session, alice.id)
    bob_counts = await count_for_user(db_session, bob.id)

    assert alice_counts == dict.fromkeys(alice_counts, 0)
    assert bob_counts == dict.fromkeys(bob_counts, 1)


async def test_shared_catalog_survives(client: AsyncClient, db_session: AsyncSession) -> None:
    """``brand_product`` and ``garment_category`` are referenced by the
    user's rows but owned by nobody — erasing an account must not take a
    bite out of the catalog."""
    user, ids = await populated_user(db_session)
    access, _ = await token_pair(db_session, user)

    assert (await erase(client, access)).status_code == 204

    assert await db_session.get(BrandProduct, ids["brand_product"]) is not None
    assert await db_session.get(GarmentCategory, MENS_BUTTON_DOWN_SHIRT_ID) is not None


# ---------------------------------------------------------------------------
# Auth, and the schema guarantee the route depends on.
# ---------------------------------------------------------------------------


async def test_delete_requires_authentication(client: AsyncClient) -> None:
    assert (await client.request("DELETE", ME, json=CONFIRM)).status_code == 401


async def test_deleting_with_a_refresh_token_is_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Erasure is irreversible, so it must not be reachable with anything
    other than a genuine access token."""
    user, _ = await populated_user(db_session)
    _, refresh = await token_pair(db_session, user)

    response = await erase(client, refresh)

    assert response.status_code == 401
    assert (await count_for_user(db_session, user.id))["user"] == 1


async def test_every_reference_to_user_cascades(db_session: AsyncSession) -> None:
    """The route erases an account with a single ``DELETE FROM "user"`` and
    lets Postgres do the rest, so every foreign key pointing at ``user``
    must be ``ON DELETE CASCADE``.

    A new table that references ``user`` with ``RESTRICT`` would make
    erasure fail outright; with ``SET NULL`` it would leave the user's rows
    behind, unowned and undeletable — a silent GDPR Art. 17 breach. This
    asserts against the live schema so either mistake fails here.
    """
    rows = (
        await db_session.execute(
            sa.text(
                """
                SELECT c.conrelid::regclass::text AS child_table, c.confdeltype
                FROM pg_constraint c
                WHERE c.contype = 'f' AND c.confrelid = '"user"'::regclass
                """
            )
        )
    ).all()

    assert {row.child_table for row in rows} == {
        "owned_garment",
        "recommendation",
        "refresh_token",
    }
    # ``confdeltype`` is a Postgres "char", which asyncpg hands back as a
    # single byte; b"c" is CASCADE.
    assert {row.confdeltype for row in rows} == {b"c"}


async def test_fit_signal_cascades_from_its_garment(db_session: AsyncSession) -> None:
    """``fit_signal`` has no ``user_id``; it is erased by a second cascade
    hop through ``owned_garment``. That hop is what keeps signals from
    outliving the account."""
    rows = (
        await db_session.execute(
            sa.text(
                """
                SELECT c.conrelid::regclass::text AS child_table, c.confdeltype
                FROM pg_constraint c
                WHERE c.contype = 'f' AND c.confrelid = 'owned_garment'::regclass
                """
            )
        )
    ).all()

    assert [(row.child_table, row.confdeltype) for row in rows] == [("fit_signal", b"c")]


async def test_erasure_requires_the_current_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """PRD §11. A leaked or borrowed access token reaches every other
    endpoint; it must not be enough on its own to destroy the account."""
    user, _ = await populated_user(db_session)
    access, _ = await token_pair(db_session, user)

    wrong = await erase(client, access, {"password": "not-the-password"})

    assert wrong.status_code == 401
    # Nothing was touched.
    assert (await count_for_user(db_session, user.id))["user"] == 1
    # And the right password still works.
    assert (await erase(client, access)).status_code == 204


async def test_erasure_body_is_required(client: AsyncClient, db_session: AsyncSession) -> None:
    user, _ = await populated_user(db_session)
    access, _ = await token_pair(db_session, user)

    response = await client.request(
        "DELETE", ME, headers={"Authorization": f"Bearer {access}"}, json={}
    )

    assert response.status_code == 422
    assert (await count_for_user(db_session, user.id))["user"] == 1


async def test_erasure_still_needs_a_valid_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The password is an additional factor, not a replacement for the token."""
    user, _ = await populated_user(db_session)

    response = await client.request("DELETE", ME, json=CONFIRM)

    assert response.status_code == 401
    assert (await count_for_user(db_session, user.id))["user"] == 1
