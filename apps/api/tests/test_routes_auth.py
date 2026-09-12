"""Integration tests for ``/auth/{signup,login,refresh}`` — TKT-P1-07.

The ticket's acceptance criteria:

* signup → login round trip
* refresh issues a new pair
* signup without the consent flag → 422

…plus the paths that make those safe: duplicate email, wrong password,
unknown email, refresh replay, and the rate limiter.

``JWT_SECRET`` is injected via an autouse fixture the same way
``test_auth_jwt.py`` does it — ``api.auth.jwt._secret()`` re-reads
settings on every call, so the env var is enough.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from _factories import TEST_PASSWORD, make_user
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import jwt as auth_jwt
from api.deps import get_session
from api.main import create_app
from api.models import RefreshToken, User

CONSENT_AT = "2026-09-09T10:30:00Z"
PASSWORD = "Str0ng-Passphrase"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-deploy-anywhere")
    yield


def signup_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "email": "alice@example.com",
        "password": PASSWORD,
        "privacy_consent_accepted_at": CONSENT_AT,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Signup.
# ---------------------------------------------------------------------------


async def test_signup_returns_token_pair(client: AsyncClient) -> None:
    response = await client.post("/auth/signup", json=signup_body())

    assert response.status_code == 201
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_token"] != payload["refresh_token"]
    assert payload["access_expires_in"] > 0
    assert payload["refresh_expires_in"] > payload["access_expires_in"]

    # Tokens are real and belong to the same subject.
    access = auth_jwt.decode(payload["access_token"])
    refresh = auth_jwt.decode(payload["refresh_token"])
    assert access.token_type is auth_jwt.TokenType.ACCESS
    assert refresh.token_type is auth_jwt.TokenType.REFRESH
    assert access.user_id == refresh.user_id


async def test_signup_persists_user_with_consent_and_hashed_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post("/auth/signup", json=signup_body(stated_fit_preference="slim"))
    assert response.status_code == 201

    user = (
        await db_session.execute(select(User).where(User.email == "alice@example.com"))
    ).scalar_one()

    # PRD §11: the consent timestamp is stored, as sent.
    assert user.privacy_consent_accepted_at == datetime(2026, 9, 9, 10, 30, tzinfo=UTC)
    assert user.stated_fit_preference == "slim"
    # PRD §11: the password is never stored in the clear.
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2id$")


async def test_signup_persists_refresh_token_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post("/auth/signup", json=signup_body())
    assert response.status_code == 201

    user = (
        await db_session.execute(select(User).where(User.email == "alice@example.com"))
    ).scalar_one()
    rows = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].revoked_at is None
    # The row stores a SHA-256 hex digest, never the token itself.
    assert rows[0].token_hash != response.json()["refresh_token"]
    assert len(rows[0].token_hash) == 64


async def test_signup_without_consent_is_422(client: AsyncClient) -> None:
    body = signup_body()
    del body["privacy_consent_accepted_at"]

    response = await client.post("/auth/signup", json=body)

    assert response.status_code == 422
    fields = {tuple(error["loc"]) for error in response.json()["detail"]}
    assert ("body", "privacy_consent_accepted_at") in fields


async def test_signup_with_null_consent_is_422(client: AsyncClient) -> None:
    # Explicit null is the other shape a client can get wrong.
    response = await client.post("/auth/signup", json=signup_body(privacy_consent_accepted_at=None))
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("password", "why"),
    [
        ("Sh0rt-1", "under 10 characters"),
        ("alllowercaseonly", "one character class"),
        ("ALLUPPERCASEONLY", "one character class"),
        ("lowercaseand1234", "two character classes"),
    ],
)
async def test_signup_rejects_weak_password(client: AsyncClient, password: str, why: str) -> None:
    response = await client.post("/auth/signup", json=signup_body(password=password))

    assert response.status_code == 422, why
    fields = {tuple(error["loc"]) for error in response.json()["detail"]}
    assert ("body", "password") in fields


async def test_signup_rejects_malformed_email(client: AsyncClient) -> None:
    response = await client.post("/auth/signup", json=signup_body(email="not-an-email"))

    assert response.status_code == 422
    fields = {tuple(error["loc"]) for error in response.json()["detail"]}
    assert ("body", "email") in fields


async def test_signup_duplicate_email_is_409(client: AsyncClient) -> None:
    assert (await client.post("/auth/signup", json=signup_body())).status_code == 201

    response = await client.post("/auth/signup", json=signup_body())

    assert response.status_code == 409


async def test_signup_duplicate_email_is_case_insensitive(client: AsyncClient) -> None:
    assert (await client.post("/auth/signup", json=signup_body())).status_code == 201

    response = await client.post("/auth/signup", json=signup_body(email="ALICE@example.com"))

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Login.
# ---------------------------------------------------------------------------


async def test_signup_then_login_round_trip(client: AsyncClient) -> None:
    """The ticket's headline acceptance criterion."""
    signup = await client.post("/auth/signup", json=signup_body())
    assert signup.status_code == 201

    login = await client.post(
        "/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
    )

    assert login.status_code == 200
    assert login.json()["access_token"] != signup.json()["access_token"]
    # Same account, new credentials.
    assert (
        auth_jwt.decode(login.json()["access_token"]).user_id
        == auth_jwt.decode(signup.json()["access_token"]).user_id
    )


async def test_login_is_case_insensitive_on_email(client: AsyncClient) -> None:
    assert (await client.post("/auth/signup", json=signup_body())).status_code == 201

    response = await client.post(
        "/auth/login", json={"email": "Alice@Example.com", "password": PASSWORD}
    )

    assert response.status_code == 200


async def test_login_wrong_password_is_401(client: AsyncClient) -> None:
    assert (await client.post("/auth/signup", json=signup_body())).status_code == 201

    response = await client.post(
        "/auth/login", json={"email": "alice@example.com", "password": "Wr0ng-Password"}
    )

    assert response.status_code == 401


async def test_login_unknown_email_is_401_with_identical_body(client: AsyncClient) -> None:
    """No user enumeration: unknown email and wrong password are indistinguishable."""
    assert (await client.post("/auth/signup", json=signup_body())).status_code == 201

    wrong_password = await client.post(
        "/auth/login", json={"email": "alice@example.com", "password": "Wr0ng-Password"}
    )
    unknown_email = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )

    assert unknown_email.status_code == wrong_password.status_code == 401
    assert unknown_email.json() == wrong_password.json()


async def test_login_short_password_is_401_not_422(client: AsyncClient) -> None:
    """Login must not leak the password policy: a guess that could not
    possibly be a valid password still fails as a credential check."""
    response = await client.post("/auth/login", json={"email": "a@example.com", "password": "x"})

    assert response.status_code == 401


async def test_login_against_a_user_with_an_unusable_hash_is_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Migration 0003 backfills pre-auth rows with a sentinel that is not a
    parseable argon2 hash. Such a row must fail closed, not crash."""
    await make_user(db_session, email="legacy@example.com", password_hash="!locked-no-password-set")

    response = await client.post(
        "/auth/login", json={"email": "legacy@example.com", "password": TEST_PASSWORD}
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Refresh.
# ---------------------------------------------------------------------------


async def test_refresh_issues_a_new_pair(client: AsyncClient) -> None:
    signup = (await client.post("/auth/signup", json=signup_body())).json()

    response = await client.post("/auth/refresh", json={"refresh_token": signup["refresh_token"]})

    assert response.status_code == 200
    rotated = response.json()
    assert rotated["refresh_token"] != signup["refresh_token"]
    assert rotated["access_token"] != signup["access_token"]
    assert (
        auth_jwt.decode(rotated["access_token"]).user_id
        == auth_jwt.decode(signup["access_token"]).user_id
    )


async def test_refresh_is_single_use(client: AsyncClient) -> None:
    signup = (await client.post("/auth/signup", json=signup_body())).json()
    assert (
        await client.post("/auth/refresh", json={"refresh_token": signup["refresh_token"]})
    ).status_code == 200

    replay = await client.post("/auth/refresh", json={"refresh_token": signup["refresh_token"]})

    assert replay.status_code == 401


async def test_refresh_replay_revokes_the_whole_chain_and_commits_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The replay response is a security action, so it must survive the
    failed request's transaction rather than being rolled back with it."""
    signup = (await client.post("/auth/signup", json=signup_body())).json()
    rotated = (
        await client.post("/auth/refresh", json={"refresh_token": signup["refresh_token"]})
    ).json()

    replay = await client.post("/auth/refresh", json={"refresh_token": signup["refresh_token"]})
    assert replay.status_code == 401

    # Every token the user holds — including the one the replay did not
    # present — is now revoked and unusable.
    user = (
        await db_session.execute(select(User).where(User.email == "alice@example.com"))
    ).scalar_one()
    rows = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert all(row.revoked_at is not None for row in rows)

    assert (
        await client.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    ).status_code == 401


async def test_refresh_rejects_an_access_token(client: AsyncClient) -> None:
    signup = (await client.post("/auth/signup", json=signup_body())).json()

    response = await client.post("/auth/refresh", json={"refresh_token": signup["access_token"]})

    assert response.status_code == 401


async def test_refresh_rejects_garbage(client: AsyncClient) -> None:
    response = await client.post("/auth/refresh", json={"refresh_token": "not.a.jwt"})

    assert response.status_code == 401


async def test_refresh_requires_a_token(client: AsyncClient) -> None:
    assert (await client.post("/auth/refresh", json={"refresh_token": ""})).status_code == 422
    assert (await client.post("/auth/refresh", json={})).status_code == 422


# ---------------------------------------------------------------------------
# Rate limiting.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def throttled_client(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    """Client whose app was built with a 2-request ``/auth/*`` budget.

    Built here rather than via the shared ``app`` fixture because the
    limiter is constructed inside ``create_app`` — the env var has to be
    set before the app exists.
    """
    monkeypatch.setenv("AUTH_RATE_LIMIT_CAPACITY", "2")
    monkeypatch.setenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", "60")
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


async def test_auth_requests_are_rate_limited(throttled_client: AsyncClient) -> None:
    body = {"email": "alice@example.com", "password": "Wr0ng-Password"}

    first = await throttled_client.post("/auth/login", json=body)
    second = await throttled_client.post("/auth/login", json=body)
    third = await throttled_client.post("/auth/login", json=body)

    assert first.status_code == 401
    assert second.status_code == 401
    assert third.status_code == 429
    assert int(third.headers["retry-after"]) >= 1


async def test_rate_limit_buckets_are_per_endpoint(throttled_client: AsyncClient) -> None:
    """Exhausting login must not lock a user out of refresh — the two have
    different abuse profiles and get their own budgets."""
    body = {"email": "alice@example.com", "password": "Wr0ng-Password"}
    for _ in range(3):
        await throttled_client.post("/auth/login", json=body)

    response = await throttled_client.post("/auth/refresh", json={"refresh_token": "not.a.jwt"})

    assert response.status_code == 401


async def test_rate_limit_does_not_apply_outside_auth(throttled_client: AsyncClient) -> None:
    for _ in range(5):
        response = await throttled_client.get("/health")
        assert response.status_code == 200


def test_password_hashing_runs_off_the_event_loop() -> None:
    """Argon2 is ~75-250 ms of pure CPU. Run inline it parks the event loop
    and stalls every other in-flight request, including the share-sheet path
    (PRD §9.2). Both call sites must go through a worker thread.
    """
    import inspect

    from api.routes import auth as auth_routes

    assert inspect.iscoroutinefunction(auth_routes._verify_credentials), (
        "_verify_credentials must be async so the argon2 verify can be awaited off-loop"
    )
    source = inspect.getsource(auth_routes)
    assert "anyio.to_thread.run_sync(auth_password.hash" in source
    assert "anyio.to_thread.run_sync(\n        auth_password.verify" in source or (
        "run_sync(auth_password.verify" in source
    )
