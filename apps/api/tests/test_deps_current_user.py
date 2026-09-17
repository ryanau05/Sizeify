"""Integration tests for the ``CurrentUser`` resolver — TKT-P1-08.

The dependency is exercised through a real request/response cycle rather
than by calling it directly: most of what it has to get right (header
parsing, 401 status, ``WWW-Authenticate``) only exists at the HTTP layer.

No production endpoint depends on ``CurrentUser`` yet — closet lands in
TKT-P1-09 — so this module mounts a throwaway protected route on the app
fixture. That keeps the test honest (full FastAPI dependency resolution,
real HTTP) without inventing an endpoint no ticket asked for.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from _api import signup_and_login
from _factories import make_user
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import jwt as auth_jwt
from api.config import get_settings
from api.deps import CurrentUser
from api.repositories.refresh_tokens import RefreshTokenRepository

PROTECTED = "/_test/protected"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-deploy-anywhere")
    get_settings.cache_clear()
    yield


class _WhoAmI(BaseModel):
    id: UUID
    email: str


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    """Standard app fixture plus one route that requires authentication."""

    @app.get(PROTECTED)
    async def _protected(user: CurrentUser) -> _WhoAmI:
        return _WhoAmI(id=user.id, email=user.email)

    return app


async def issue_access_token(session: AsyncSession, user_id: UUID) -> str:
    access, _ = await auth_jwt.issue_pair(user_id, RefreshTokenRepository(session))
    return access


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# The ticket's acceptance criterion.
# ---------------------------------------------------------------------------


async def test_protected_endpoint_401s_without_a_token(client: AsyncClient) -> None:
    response = await client.get(PROTECTED)

    assert response.status_code == 401
    # RFC 6750 §3: a challenge for a request that carried no credentials
    # must not claim an error — there was nothing to be wrong.
    assert response.headers["www-authenticate"] == "Bearer"


async def test_protected_endpoint_200s_with_a_valid_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, email="alice@example.com")
    token = await issue_access_token(db_session, user.id)

    response = await client.get(PROTECTED, headers=bearer(token))

    assert response.status_code == 200
    assert response.json() == {"id": str(user.id), "email": "alice@example.com"}


async def test_resolver_works_end_to_end_from_signup(client: AsyncClient) -> None:
    """The token a real client actually holds authenticates a real request."""
    access = await signup_and_login(client)

    response = await client.get(PROTECTED, headers=bearer(access))

    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"


# ---------------------------------------------------------------------------
# Distinguishable failures: missing / malformed / expired.
# ---------------------------------------------------------------------------


async def test_missing_malformed_and_expired_are_distinguishable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """TKT-P1-08 asks for 401s a client can act on. These three are the
    ones with different remedies: log in, fix the header, refresh."""
    user = await make_user(db_session)
    expired = _expired_access_token(user.id)

    missing = await client.get(PROTECTED)
    malformed = await client.get(PROTECTED, headers={"Authorization": "Bearer"})
    stale = await client.get(PROTECTED, headers=bearer(expired))

    assert missing.status_code == malformed.status_code == stale.status_code == 401
    details = {response.json()["detail"] for response in (missing, malformed, stale)}
    assert len(details) == 3, details
    # Each names its own remedy.
    assert "Authorization" in missing.json()["detail"]
    assert "Malformed" in malformed.json()["detail"]
    assert "/auth/refresh" in stale.json()["detail"]


async def test_expired_token_challenge_reports_invalid_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)

    response = await client.get(PROTECTED, headers=bearer(_expired_access_token(user.id)))

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


@pytest.mark.parametrize(
    "header",
    [
        "Bearer",
        "Bearer ",
        "Basic YWxpY2U6c2VjcmV0",
        "token abc.def.ghi",
        "abc.def.ghi",
    ],
)
async def test_unusable_authorization_headers_are_401(client: AsyncClient, header: str) -> None:
    response = await client.get(PROTECTED, headers={"Authorization": header})

    assert response.status_code == 401
    assert 'error="invalid_request"' in response.headers["www-authenticate"]


@pytest.mark.parametrize(
    "header",
    [
        'Quoted" injected="yes',
        "Basic YWxpY2U6c2VjcmV0",
        "eyJhbGciOiJIUzI1NiJ9.payload.signature",
        "Bearer\ttab-separated",
    ],
)
async def test_challenge_never_reflects_the_submitted_header(
    client: AsyncClient, header: str
) -> None:
    """``WWW-Authenticate`` puts ``error_description`` inside a quoted
    string, so echoing caller-controlled bytes there would let a crafted
    header break the quoting — and echoing a whole token back is its own
    small leak. Nothing from the request may appear in the challenge."""
    response = await client.get(PROTECTED, headers={"Authorization": header})

    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert header not in challenge
    assert header not in response.json()["detail"]
    # Still exactly two quoted parameters — no attacker-introduced third.
    assert challenge.count('"') == 4


# ---------------------------------------------------------------------------
# Indistinguishable failures: nothing may leak about the account.
# ---------------------------------------------------------------------------


async def test_garbage_token_is_401(client: AsyncClient) -> None:
    response = await client.get(PROTECTED, headers=bearer("not.a.jwt"))

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


async def test_tampered_token_is_401(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    token = await issue_access_token(db_session, user.id)
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-4]}AAAA"

    response = await client.get(PROTECTED, headers=bearer(tampered))

    assert response.status_code == 401


async def test_token_signed_with_another_secret_is_401(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await make_user(db_session)
    monkeypatch.setenv("JWT_SECRET", "a-different-secret-of-a-respectable-length")
    get_settings.cache_clear()
    foreign = await issue_access_token(db_session, user.id)
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-deploy-anywhere")
    get_settings.cache_clear()

    response = await client.get(PROTECTED, headers=bearer(foreign))

    assert response.status_code == 401


async def test_refresh_token_cannot_authenticate_a_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A refresh token is a valid signed JWT, just not a credential for
    this surface. It must not be accepted, and must not be told apart from
    an outright forgery."""
    user = await make_user(db_session)
    _, refresh = await auth_jwt.issue_pair(user.id, RefreshTokenRepository(db_session))

    with_refresh = await client.get(PROTECTED, headers=bearer(refresh))
    with_garbage = await client.get(PROTECTED, headers=bearer("not.a.jwt"))

    assert with_refresh.status_code == 401
    assert with_refresh.json() == with_garbage.json()


async def test_deleted_user_is_401_indistinguishable_from_a_forgery(
    client: AsyncClient,
) -> None:
    """A correctly-signed token naming a user id that no longer exists must
    look exactly like a bad token — otherwise the endpoint reports which
    user ids are real."""
    ghost = _token_for_nonexistent_user()

    for_ghost = await client.get(PROTECTED, headers=bearer(ghost))
    for_garbage = await client.get(PROTECTED, headers=bearer("not.a.jwt"))

    assert for_ghost.status_code == 401
    assert for_ghost.json() == for_garbage.json()


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _expired_access_token(user_id: UUID) -> str:
    """Mint an access token whose ``exp`` is already in the past.

    Reaches into ``_encode`` with a negative TTL rather than freezing the
    clock: pyjwt reads the system clock during ``decode``, so a fake clock
    would have to be installed inside the library to have any effect.
    """
    return auth_jwt._encode(user_id, auth_jwt.TokenType.ACCESS, ttl_seconds=-60)


def _token_for_nonexistent_user() -> str:
    """A well-formed, correctly-signed access token whose ``sub`` names a
    user id that was never inserted."""
    return auth_jwt._encode(uuid4(), auth_jwt.TokenType.ACCESS, ttl_seconds=300)
