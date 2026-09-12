"""``/auth/signup``, ``/auth/login``, ``/auth/refresh`` — TKT-P1-07.

Thin handlers over ``api.auth.password`` (TKT-P1-05) and ``api.auth.jwt``
(TKT-P1-06). All three return the same ``TokenPair``, so a client has one
code path for "I now hold credentials" regardless of how it got there.

Rate limiting for this router is applied app-wide by
``api.rate_limit.RateLimitMiddleware`` (installed in ``api.main``) rather
than per-route, so a future auth endpoint is covered the moment it is
mounted under ``/auth/`` instead of when someone remembers a decorator.

Error-response policy
---------------------
Login returns one indistinguishable 401 for "no such email" and "wrong
password", and spends the same argon2 work in both cases (see
``_verify_credentials``). Signup necessarily leaks that an address is
taken — it cannot both refuse duplicates and hide them — so the leak is
confined to that one endpoint rather than spread across all three. The
mitigation for signup is the rate limiter, not the response body.

Transactions
------------
Each handler wraps its writes in ``transaction(...)``, which commits on
clean exit and rolls back on exception. The one case that is easy to get
wrong is refresh-token replay: ``jwt.rotate`` revokes the user's whole
chain and *then* raises, so that revocation must be committed, not rolled
back with the failed request. ``refresh`` below catches the error inside
the transaction for exactly that reason.
"""

import logging
from typing import Annotated

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError

from api.auth import jwt as auth_jwt
from api.auth import password as auth_password
from api.config import Settings, get_settings
from api.deps import RefreshTokenRepositoryDep, UserRepositoryDep
from api.models import User
from api.repositories.base import transaction
from api.schemas.auth import LoginRequest, RefreshRequest, SignupRequest, TokenPair

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

SettingsDep = Annotated[Settings, Depends(get_settings)]

# Argon2 hash of a value nobody can submit. Login verifies against this
# when the email is unknown, so response time does not depend on whether
# the account exists — without it, a fast 401 is an account oracle no
# matter how careful the response body is. Computed once at import so the
# cost lands at startup, not on the first unknown-email login.
_DUMMY_PASSWORD_HASH = auth_password.hash("sizeify-timing-equalizer-not-a-credential")


# Raised as fresh instances rather than module-level singletons: a shared
# exception object accumulates ``__traceback__`` references to every
# request that raised it.
def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _invalid_refresh() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _email_taken() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="An account with that email already exists.",
    )


def _token_pair(access: str, refresh: str, settings: Settings) -> TokenPair:
    """Wrap an issued pair with the TTLs the client needs to schedule its
    own rotation, so it never has to decode the JWT to find ``exp``."""
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        access_expires_in=settings.access_token_ttl,
        refresh_expires_in=settings.refresh_token_ttl,
    )


async def _verify_credentials(user: User | None, submitted_password: str) -> User:
    """Return ``user`` if the password checks out, else raise 401.

    Always runs exactly one argon2 verify — against
    ``_DUMMY_PASSWORD_HASH`` when the user is unknown — so both branches
    cost the same wall-clock time.

    The verify runs in a worker thread. Argon2 is deliberately expensive
    (~75 ms here, ~250 ms on the slower hardware ``auth.password`` is
    calibrated for) and it is pure CPU, so calling it inline would park
    the event loop for that whole window and stall every other request on
    the worker — including the share-sheet path, which has a p95 < 5 s
    budget to hit (PRD §9.2). CLAUDE.md bans a *sync DB call* on that path
    for the same reason; 250 ms of hashing is the larger offender.
    """
    candidate_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    matched = await anyio.to_thread.run_sync(
        auth_password.verify, submitted_password, candidate_hash
    )
    if user is None or not matched:
        # No email in the payload: a failed-login log that records the address
        # tried is a credential-stuffing target list sitting in the log store
        # (PRD §11 data minimization). ``known_account`` is enough to tell a
        # spray against random addresses from a targeted attempt on a real one.
        logger.warning(
            "auth.login.failed",
            extra={
                "known_account": user is not None,
                "user_id": str(user.id) if user is not None else None,
            },
        )
        raise _invalid_credentials()
    return user


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    users: UserRepositoryDep,
    refresh_tokens: RefreshTokenRepositoryDep,
    settings: SettingsDep,
) -> TokenPair:
    """Create an account and return its first token pair.

    ``privacy_consent_accepted_at`` is required by ``SignupRequest``, so a
    body without it is a 422 before this handler runs — there is no path
    that creates a user without a consent record (PRD §11).
    """
    # Hash before touching the session, and off the event loop. Argon2 is a
    # deliberate ~75-250 ms of pure CPU: there is no reason to hold a DB
    # transaction open across it, and no reason to block every other
    # in-flight request either (see ``_verify_credentials``).
    password_hash = await anyio.to_thread.run_sync(auth_password.hash, body.password)

    try:
        async with transaction(users.session):
            if await users.get_by_email(body.email) is not None:
                raise _email_taken()
            user = await users.create(
                email=body.email,
                password_hash=password_hash,
                privacy_consent_accepted_at=body.privacy_consent_accepted_at,
                stated_fit_preference=body.stated_fit_preference,
            )
            access, refresh = await auth_jwt.issue_pair(user.id, refresh_tokens)
    except IntegrityError as exc:
        # The ``get_by_email`` check above loses to a concurrent signup for
        # the same address; the UNIQUE index is what actually decides. Map
        # the loser onto the same 409 the check would have produced.
        raise _email_taken() from exc

    return _token_pair(access, refresh, settings)


@router.post("/login")
async def login(
    body: LoginRequest,
    users: UserRepositoryDep,
    refresh_tokens: RefreshTokenRepositoryDep,
    settings: SettingsDep,
) -> TokenPair:
    """Exchange email + password for a fresh token pair."""
    user = await _verify_credentials(await users.get_by_email(body.email), body.password)

    async with transaction(users.session):
        access, refresh = await auth_jwt.issue_pair(user.id, refresh_tokens)

    return _token_pair(access, refresh, settings)


@router.post("/refresh")
async def refresh(
    body: RefreshRequest,
    refresh_tokens: RefreshTokenRepositoryDep,
    settings: SettingsDep,
) -> TokenPair:
    """Rotate a refresh token into a new pair. Single-use (TKT-P1-06).

    Replaying an already-rotated token revokes every refresh token the
    user holds and returns 401.
    """
    replayed = False
    try:
        async with transaction(refresh_tokens.session):
            try:
                access, new_refresh = await auth_jwt.rotate(body.refresh_token, refresh_tokens)
            except auth_jwt.ReusedRefreshTokenError as exc:
                # Caught *inside* the transaction on purpose. ``rotate``
                # has already revoked the user's whole chain in this
                # session; that revocation is the security response to a
                # probable token theft and must commit. Letting the error
                # escape the block would roll it back along with the
                # failed request and defeat the mechanism entirely.
                # Probable token theft. This is the event worth paging on:
                # the user's entire chain has just been revoked and they are
                # about to be logged out of every device.
                logger.warning("auth.refresh.replayed", extra={"user_id": str(exc.user_id)})
                replayed = True
    except (auth_jwt.ExpiredTokenError, auth_jwt.InvalidTokenError):
        # These are raised before ``rotate`` writes anything, so the
        # rollback ``transaction`` just performed discards nothing.
        raise _invalid_refresh() from None

    if replayed:
        raise _invalid_refresh()

    return _token_pair(access, new_refresh, settings)
