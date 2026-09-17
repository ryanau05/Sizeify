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

import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from _factories import TEST_PASSWORD, make_user
from _keys import TEST_JWT_SECRET
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import jwt as auth_jwt
from api.auth import password as auth_password
from api.config import get_settings
from api.deps import get_session
from api.main import create_app
from api.models import RefreshToken, User
from api.repositories.refresh_tokens import RefreshTokenRepository
from api.repositories.users import UserRepository

CONSENT_AT = "2026-09-09T10:30:00Z"
PASSWORD = "Str0ng-Passphrase"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    get_settings.cache_clear()
    yield


async def _register_and_sign_in(client: AsyncClient) -> dict[str, Any]:
    """Register then sign in, returning the login token pair.

    Signup issues no tokens on purpose (see ``SignupAccepted``), so the tests
    that need a refresh token get it from the login that follows.
    """
    assert (await client.post("/auth/signup", json=signup_body())).status_code == 202
    login = await client.post(
        "/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    return dict(login.json())


def signup_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "email": "alice@example.com",
        "password": PASSWORD,
        "privacy_consent_accepted_at": CONSENT_AT,
    }
    body.update(overrides)
    return body


# --- call sites that do argon2 work, for the off-loop test below ----------


async def _signup_request(client: AsyncClient, session: AsyncSession) -> None:
    """Hashes the new password."""
    await client.post("/auth/signup", json=signup_body(email="offloop-signup@example.com"))


async def _login_request(client: AsyncClient, session: AsyncSession) -> None:
    """Verifies the stored hash, and may re-hash on a parameter upgrade."""
    user = await make_user(session, email="offloop-login@example.com")
    await client.post("/auth/login", json={"email": user.email, "password": TEST_PASSWORD})


async def _delete_me_request(client: AsyncClient, session: AsyncSession) -> None:
    """Re-authenticates before erasing the account — the call site the old
    source-text assertion never looked at."""
    user = await make_user(session, email="offloop-delete@example.com")
    access, _ = await auth_jwt.issue_pair(user.id, RefreshTokenRepository(session))
    await client.request(
        "DELETE",
        "/me",
        json={"password": TEST_PASSWORD},
        headers={"Authorization": f"Bearer {access}"},
    )


# ---------------------------------------------------------------------------
# Signup.
# ---------------------------------------------------------------------------


async def test_signup_returns_no_tokens(client: AsyncClient) -> None:
    """202 with a fixed body, and deliberately no token pair.

    A pair can only be issued for an account we just created, so its presence
    would answer "did this address already exist" — the oracle the uniform
    response exists to close.
    """
    response = await client.post("/auth/signup", json=signup_body())

    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"detail"}
    assert "access_token" not in body
    assert "refresh_token" not in body


async def test_signup_persists_user_with_consent_and_hashed_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post("/auth/signup", json=signup_body(stated_fit_preference="slim"))
    assert response.status_code == 202

    user = (
        await db_session.execute(select(User).where(User.email == "alice@example.com"))
    ).scalar_one()

    # PRD §11: the consent timestamp is stored, as sent.
    assert user.privacy_consent_accepted_at == datetime(2026, 9, 9, 10, 30, tzinfo=UTC)
    assert user.stated_fit_preference == "slim"
    # PRD §11: the password is never stored in the clear.
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2id$")


async def test_signup_does_not_mint_a_refresh_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The token chain starts at the first login, not at signup — signup has
    no tokens to persist."""
    assert (await client.post("/auth/signup", json=signup_body())).status_code == 202

    user = (
        await db_session.execute(select(User).where(User.email == "alice@example.com"))
    ).scalar_one()
    rows = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)))
        .scalars()
        .all()
    )
    assert rows == []

    # …and the first login starts it.
    assert (
        await client.post("/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    ).status_code == 200
    rows = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].revoked_at is None
    # The row stores a SHA-256 hex digest, never the token itself.
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


async def test_signup_does_not_reveal_that_an_address_is_taken(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The finding this closes: signup used to answer 409 for a registered
    address, which told any prober who has an account here. Login is
    timing-equalized against exactly that, so signup undercut it."""
    first = await client.post("/auth/signup", json=signup_body())
    second = await client.post("/auth/signup", json=signup_body())
    fresh = await client.post("/auth/signup", json=signup_body(email="nobody@example.com"))

    assert first.status_code == second.status_code == fresh.status_code == 202
    assert first.json() == second.json() == fresh.json()

    # And the duplicate did not overwrite the original account's password.
    assert (
        await client.post("/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    ).status_code == 200
    assert (
        await db_session.execute(
            select(sa.func.count()).select_from(User).where(User.email == "alice@example.com")
        )
    ).scalar_one() == 1


async def test_a_taken_address_cannot_be_hijacked_by_signing_up_again(
    client: AsyncClient,
) -> None:
    """Silence must not mean "overwrite". A second signup for a live address
    with a different password must leave the original credentials intact."""
    assert (await client.post("/auth/signup", json=signup_body())).status_code == 202
    assert (
        await client.post("/auth/signup", json=signup_body(password="Attacker-Chosen-1"))
    ).status_code == 202

    assert (
        await client.post(
            "/auth/login", json={"email": "alice@example.com", "password": "Attacker-Chosen-1"}
        )
    ).status_code == 401
    assert (
        await client.post("/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    ).status_code == 200


async def test_signup_is_case_insensitive_about_existing_addresses(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    assert (await client.post("/auth/signup", json=signup_body())).status_code == 202

    variant = await client.post("/auth/signup", json=signup_body(email="ALICE@example.com"))

    assert variant.status_code == 202
    assert (
        await db_session.execute(
            select(sa.func.count())
            .select_from(User)
            .where(sa.func.lower(User.email) == "alice@example.com")
        )
    ).scalar_one() == 1


async def test_signup_losing_the_unique_race_is_still_202(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent signups for one address: the loser's pre-check read
    "free" and the UNIQUE index decided otherwise.

    Simulated by blinding ``get_by_email`` — the handler then walks exactly
    the path the loser of a real race walks, into the ``IntegrityError`` the
    INSERT raises. It has to answer with the same 202 as every other signup,
    because a 500 here is the account-existence oracle the uniform response
    exists to close, reachable by anyone willing to send two requests at once.
    """
    await make_user(db_session, email="alice@example.com")

    async def _sees_nothing(self: UserRepository, email: str) -> User | None:
        return None

    monkeypatch.setattr(UserRepository, "get_by_email", _sees_nothing)

    response = await client.post("/auth/signup", json=signup_body())

    assert response.status_code == 202
    monkeypatch.undo()

    # Byte-identical to an ordinary signup for a free address: the race
    # loser is not distinguishable from the winner by anything a client sees.
    ordinary = await client.post("/auth/signup", json=signup_body(email="nobody@example.com"))
    assert ordinary.status_code == 202
    assert response.json() == ordinary.json()


async def test_signup_then_login_round_trip(client: AsyncClient) -> None:
    """The ticket's headline acceptance criterion, and what a client's "sign
    up" button now does: register, then immediately sign in."""
    registered = await client.post("/auth/signup", json=signup_body())
    assert registered.status_code == 202

    login = await client.post(
        "/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
    )

    assert login.status_code == 200
    assert login.json()["access_token"]
    assert auth_jwt.decode(login.json()["access_token"]).token_type is auth_jwt.TokenType.ACCESS


async def test_login_is_case_insensitive_on_email(client: AsyncClient) -> None:
    assert (await client.post("/auth/signup", json=signup_body())).status_code == 202

    response = await client.post(
        "/auth/login", json={"email": "Alice@Example.com", "password": PASSWORD}
    )

    assert response.status_code == 200


async def test_login_wrong_password_is_401(client: AsyncClient) -> None:
    assert (await client.post("/auth/signup", json=signup_body())).status_code == 202

    response = await client.post(
        "/auth/login", json={"email": "alice@example.com", "password": "Wr0ng-Password"}
    )

    assert response.status_code == 401


async def test_login_unknown_email_is_401_with_identical_body(client: AsyncClient) -> None:
    """No user enumeration: unknown email and wrong password are indistinguishable."""
    assert (await client.post("/auth/signup", json=signup_body())).status_code == 202

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
    signup = await _register_and_sign_in(client)

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
    signup = await _register_and_sign_in(client)
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
    signup = await _register_and_sign_in(client)
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
    signup = await _register_and_sign_in(client)

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
    # Pin the value, not a floor: _send_429 builds the header as
    # str(max(1, ceil(retry_after))), so ">= 1" held by construction for every
    # possible input and could only ever catch the header disappearing. With
    # capacity 2 over 60s the bucket refills one token every 30s.
    assert third.headers["retry-after"] == "30"


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


@pytest.mark.parametrize(
    ("name", "call"),
    [
        ("signup", _signup_request),
        ("login", _login_request),
        ("delete_me", _delete_me_request),
    ],
)
async def test_password_hashing_runs_off_the_event_loop(
    name: str,
    call: Callable[[AsyncClient, AsyncSession], Awaitable[None]],
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Argon2 is ~75-250 ms of pure CPU. Run inline it parks the event loop
    and stalls every other in-flight request, including the share-sheet path
    (PRD §9.2). Every call site must go through a worker thread.

    This used to assert on the module's *source text* via ``inspect.getsource``,
    which is not a test of behaviour: it passed if the off-loop call were
    commented out or moved into dead code, failed on any harmless refactor
    (extracting a helper, renaming the alias, ruff reflowing the arguments),
    and only ever read ``api.routes.auth`` — so the identical argon2 verify
    behind ``DELETE /me`` was never covered at all.

    Recording the thread each hash actually runs on covers all three call
    sites and cannot be satisfied by a comment.
    """
    main_thread = threading.get_ident()
    threads: list[int] = []

    for fn_name in ("hash", "verify", "needs_rehash"):
        original = getattr(auth_password, fn_name)

        def recording(*args: Any, _original: Any = original, **kwargs: Any) -> Any:
            threads.append(threading.get_ident())
            return _original(*args, **kwargs)

        monkeypatch.setattr(auth_password, fn_name, recording)

    await call(client, db_session)

    assert threads, f"{name} did no argon2 work — the test is not exercising the path"
    assert main_thread not in threads, (
        f"{name} ran argon2 on the event loop thread, parking it for ~{len(threads) * 75}ms"
    )


async def test_login_upgrades_a_weakly_hashed_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Re-calibrating the argon2 parameters only helps existing accounts if
    login rehashes them; otherwise every current user keeps the weaker hash
    forever and there is no reset flow to recover through."""
    from argon2 import PasswordHasher, Type
    from sqlalchemy import select

    from api.auth import password as auth_password
    from api.models import User

    weak = PasswordHasher(
        time_cost=1,
        memory_cost=8,
        parallelism=1,
        hash_len=auth_password.HASH_LEN_BYTES,
        salt_len=auth_password.SALT_LEN_BYTES,
        type=Type.ID,
    ).hash(PASSWORD)
    await make_user(db_session, email="legacy@example.com", password_hash=weak)

    response = await client.post(
        "/auth/login", json={"email": "legacy@example.com", "password": PASSWORD}
    )

    assert response.status_code == 200
    stored = (
        await db_session.execute(select(User).where(User.email == "legacy@example.com"))
    ).scalar_one()
    assert stored.password_hash != weak
    assert auth_password.needs_rehash(stored.password_hash) is False
    assert auth_password.verify(PASSWORD, stored.password_hash) is True


async def test_signup_does_not_swallow_an_unrelated_constraint_violation(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an email collision may take the silent 202 path.

    Catching every ``IntegrityError`` meant any other constraint — a future
    CHECK, FK, or NOT NULL — also answered "account has been created", created
    nothing, and logged it as an existing address. Because signup is
    deliberately non-enumerable, the user could not tell that apart from a
    taken address: 202 here, then 401 on every login attempt, permanently,
    with no signal that anything had broken.
    """
    original_create = UserRepository.create

    async def _violates_something_else(self: UserRepository, **kwargs: Any) -> User:
        await original_create(self, **kwargs)
        raise IntegrityError(
            "INSERT INTO ...",
            {},
            Exception('violates check constraint "user_some_future_check"'),
        )

    monkeypatch.setattr(UserRepository, "create", _violates_something_else)

    with pytest.raises(IntegrityError):
        await client.post("/auth/signup", json=signup_body(email="unrelated@example.com"))
