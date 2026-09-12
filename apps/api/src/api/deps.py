"""FastAPI request-scoped dependencies.

Two layers:

* ``get_session`` yields a per-request ``AsyncSession``. **Only the repository
  layer and the migrations env touch this directly** — route handlers depend
  on the repository providers below, never on ``get_session``.
* ``get_*_repository`` builds a per-entity repository bound to the current
  request's session. FastAPI caches dependency results within a request, so
  multiple repos in one handler share the same session — multi-repo writes
  wrapped in ``transaction(session)`` land atomically.
* ``CurrentUser`` resolves the ``Authorization: Bearer`` access token to a
  ``User`` row, 401-ing on any failure (TKT-P1-08). Every closet and ``/me``
  endpoint depends on it; ``/auth/*`` and ``/health`` do not.

The ``*Dep`` ``Annotated`` aliases are the canonical call-site form::

    @router.get("/closet/garments")
    async def list_garments(user: CurrentUser, repo: OwnedGarmentRepositoryDep) -> ...:
        ...
"""

import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import jwt as auth_jwt
from api.models import User
from api.repositories import (
    BrandProductRepository,
    FitSignalRepository,
    GarmentCategoryRepository,
    OwnedGarmentRepository,
    RecommendationRepository,
    RefreshTokenRepository,
    UserRepository,
)
from api.repositories.base import get_sessionmaker

logger = logging.getLogger(__name__)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a per-request ``AsyncSession``, rolling back on error.

    The session is closed at request end. Any active transaction is rolled
    back if the handler raises; commit is the caller's responsibility (via
    ``api.repositories.base.transaction`` or an explicit ``session.commit()``
    inside the repository layer).
    """
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# Internal alias — route handlers should NOT use this directly. Repository
# providers below depend on it; everything else goes through them.
_SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_user_repository(session: _SessionDep) -> UserRepository:
    return UserRepository(session)


async def get_garment_category_repository(
    session: _SessionDep,
) -> GarmentCategoryRepository:
    return GarmentCategoryRepository(session)


async def get_owned_garment_repository(
    session: _SessionDep,
) -> OwnedGarmentRepository:
    return OwnedGarmentRepository(session)


async def get_fit_signal_repository(session: _SessionDep) -> FitSignalRepository:
    return FitSignalRepository(session)


async def get_brand_product_repository(
    session: _SessionDep,
) -> BrandProductRepository:
    return BrandProductRepository(session)


async def get_recommendation_repository(
    session: _SessionDep,
) -> RecommendationRepository:
    return RecommendationRepository(session)


async def get_refresh_token_repository(
    session: _SessionDep,
) -> RefreshTokenRepository:
    return RefreshTokenRepository(session)


# Annotated aliases for route handlers. Importing these instead of the
# ``get_*`` callables keeps signatures readable and consistent.
UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]
GarmentCategoryRepositoryDep = Annotated[
    GarmentCategoryRepository, Depends(get_garment_category_repository)
]
OwnedGarmentRepositoryDep = Annotated[OwnedGarmentRepository, Depends(get_owned_garment_repository)]
FitSignalRepositoryDep = Annotated[FitSignalRepository, Depends(get_fit_signal_repository)]
BrandProductRepositoryDep = Annotated[BrandProductRepository, Depends(get_brand_product_repository)]
RecommendationRepositoryDep = Annotated[
    RecommendationRepository, Depends(get_recommendation_repository)
]
RefreshTokenRepositoryDep = Annotated[RefreshTokenRepository, Depends(get_refresh_token_repository)]


# ---------------------------------------------------------------------------
# Current user (TKT-P1-08)
# ---------------------------------------------------------------------------
#
# Every failure below is a 401 carrying an RFC 6750 ``WWW-Authenticate``
# challenge, so a client can tell *why* it was rejected without guessing:
# an expired access token means "rotate via /auth/refresh", a malformed one
# means "your header is wrong", and a missing one means "log in". Getting
# this wrong is how clients end up retrying a login loop against an
# expiry that a refresh would have fixed.
#
# What the messages deliberately do NOT distinguish is anything about the
# *account*. A signature that doesn't verify, a token whose ``sub`` names a
# deleted user, and a refresh token presented as an access token all
# collapse to the same "invalid token" response — otherwise the endpoint
# becomes an oracle for which user ids exist.


def _unauthorized(detail: str, *, error: str | None = None) -> HTTPException:
    """401 with an RFC 6750 bearer challenge.

    ``error`` is omitted when no credentials were supplied at all — RFC 6750
    §3 says a challenge for a request that bore no token must not carry an
    error code, since there is nothing to report an error about.
    """
    challenge = "Bearer"
    if error is not None:
        challenge = f'Bearer error="{error}", error_description="{detail}"'
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": challenge},
    )


def _invalid_token() -> HTTPException:
    return _unauthorized("Invalid access token.", error="invalid_token")


class _BearerScheme(HTTPBearer):
    """``Authorization: Bearer <token>`` parser.

    Subclasses ``HTTPBearer`` for the OpenAPI security scheme (the /docs
    "Authorize" button, and a ``security`` entry on every protected
    operation) but replaces ``__call__`` for two reasons:

    * The base class collapses "no header" and "malformed header" into one
      response, and TKT-P1-08 asks for those to be distinguishable.
    * The base class does not check that the scheme is actually ``bearer``,
      so ``Authorization: Basic <...>`` is passed through as though it were
      a token and fails later as a JWT parse error.
    """

    def __init__(self) -> None:
        # ``scheme_name`` is pinned because the default is the class name,
        # which would put a private ``_BearerScheme`` into the published
        # OpenAPI document and into every generated client.
        super().__init__(scheme_name="BearerAuth", bearerFormat="JWT", auto_error=False)

    async def __call__(self, request: Request) -> HTTPAuthorizationCredentials:
        header = request.headers.get("Authorization")
        if header is None:
            raise _unauthorized(
                "Not authenticated. Supply an 'Authorization: Bearer <access token>' header."
            )

        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            # The submitted scheme is deliberately NOT echoed. Everything
            # in ``_unauthorized`` lands inside a quoted string in the
            # ``WWW-Authenticate`` header, so reflecting caller-controlled
            # bytes there invites header injection — and a header with no
            # space at all makes ``scheme`` the entire value, which would
            # mean echoing a whole token back. The remedy is the same
            # either way, so state it without quoting the input.
            raise _unauthorized(
                "Unsupported authorization scheme. Use 'Bearer <access token>'.",
                error="invalid_request",
            )
        if not token.strip():
            raise _unauthorized(
                "Malformed Authorization header: expected 'Bearer <access token>'.",
                error="invalid_request",
            )

        return HTTPAuthorizationCredentials(scheme=scheme, credentials=token.strip())


bearer_scheme = _BearerScheme()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    users: UserRepositoryDep,
) -> User:
    """Resolve the bearer access token to its ``User`` row, or 401.

    Access tokens are validated statelessly — signature and ``exp`` only, no
    refresh-token lookup — because that is the whole point of the short TTL
    (see ``api.auth.jwt``). The one DB hit is loading the user, which
    handlers need anyway.
    """
    try:
        claims = auth_jwt.decode(credentials.credentials)
    except auth_jwt.ExpiredTokenError:
        # Worth naming precisely: this is the one failure a client can fix
        # on its own, by rotating through POST /auth/refresh.
        raise _unauthorized(
            "Access token has expired. Obtain a new one via POST /auth/refresh.",
            error="invalid_token",
        ) from None
    except auth_jwt.InvalidTokenError:
        raise _invalid_token() from None

    if claims.token_type is not auth_jwt.TokenType.ACCESS:
        # A refresh token is a valid, unexpired, correctly-signed JWT — it
        # is simply not a credential for this surface. Same response as any
        # other bad token: naming the mistake would confirm the token is
        # genuine.
        raise _invalid_token()

    user = await users.get(claims.user_id)
    if user is None:
        # Signed token, but the account is gone (TKT-P1-18's DELETE /me, or
        # a token minted under a since-restored database). Indistinguishable
        # from a bad signature on purpose — see the note above.
        #
        # Worth logging even so: a correctly-signed token for a user that does
        # not exist means either a normal post-erasure request or a signing key
        # that outlived its database, and those need telling apart.
        logger.warning("auth.token.unknown_subject", extra={"user_id": str(claims.user_id)})
        raise _invalid_token()

    return user


#: Canonical call-site form for protected handlers.
CurrentUser = Annotated[User, Depends(get_current_user)]


#: OpenAPI documentation for the 401 every ``CurrentUser`` handler can
#: return. FastAPI infers the security requirement from the dependency but
#: not the failure response, so protected routes spread this into their
#: decorator to keep the generated docs honest::
#:
#:     @router.get("/closet/garments", responses=UNAUTHORIZED_RESPONSE)
class ErrorDetail(BaseModel):
    """The body FastAPI renders for a raised ``HTTPException``.

    Declared so generated clients get a type for it. Note this is a different
    shape from a 422, whose ``detail`` is a *list* of per-field entries —
    which is exactly the kind of thing a typed client needs told.
    """

    detail: str


#: Reusable OpenAPI ``responses`` entries. Spread into route decorators::
#:
#:     @router.get("/closet/garments", responses={**UNAUTHORIZED_RESPONSE})
UNAUTHORIZED_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {
        "model": ErrorDetail,
        "description": (
            "Missing, malformed, expired, or otherwise unusable access token. "
            "The 'WWW-Authenticate' header carries an RFC 6750 error code."
        ),
    }
}

#: Every ``/auth/*`` path can be throttled by ``api.rate_limit``, which runs as
#: middleware and so is invisible to FastAPI's response inference.
RATE_LIMITED_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_429_TOO_MANY_REQUESTS: {
        "model": ErrorDetail,
        "description": (
            "Too many requests from this client. The 'Retry-After' header "
            "carries the number of whole seconds to wait."
        ),
    }
}
