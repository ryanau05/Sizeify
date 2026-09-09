"""JWT issuance, validation, and refresh rotation — TKT-P1-06.

Two flavors of token, both HS256-signed JWTs:

* **Access** — short-lived (``settings.access_token_ttl``, default 15
  min). Stateless: validation is signature + ``exp`` only, no DB hit.
  Carried in ``Authorization: Bearer`` on every protected request.
* **Refresh** — long-lived (``settings.refresh_token_ttl``, default 30
  d). Single-use: the JWT's SHA-256 is recorded in ``refresh_token`` so
  rotation can mark it revoked. Presenting an already-revoked refresh
  is the replay signal — ``rotate`` revokes the user's whole chain and
  raises ``ReusedRefreshTokenError`` (PRD §11 / TKT-P1-06 acceptance).

Why hash, not JTI
-----------------
The DB row stores ``sha256(jws_string)`` rather than the JWT's ``jti``
claim. A DB dump then yields neither usable tokens nor token-to-hash
correlations that survive outside the DB. The JWT is self-validating;
the row exists only to track rotation state.

Key rotation hook
-----------------
Every JWT this module issues carries a ``kid`` header set to
``KEY_ID`` so a future key rotation can route tokens to the right
verifying secret:

1. Add the new secret (e.g. ``JWT_SECRET_V2``) to ``api.config``
   alongside the old one.
2. Bump ``KEY_ID`` so newly-issued tokens carry the new ``kid``.
3. Make ``_secret_for(kid)`` look up the right secret per header.
4. After the longest refresh TTL has elapsed since the bump, drop the
   old secret.

Phase 1 ships with a single key; the ``kid`` header and the indirection
through ``_secret()`` are the seam where the rotation extension lands.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import jwt as pyjwt

from api.config import get_settings
from api.repositories.refresh_tokens import RefreshTokenRepository

# Active key identifier — placed in the JWT header so a verifier can
# select the right secret. Bump when rotating keys (see module docstring).
KEY_ID = "v1"
ALGORITHM = "HS256"


class TokenType(StrEnum):
    """``typ`` claim. Distinguishes access vs refresh at validation time."""

    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True)
class Claims:
    """Decoded JWT payload. All timestamps tz-aware UTC."""

    user_id: UUID
    token_type: TokenType
    issued_at: datetime
    expires_at: datetime
    jti: str
    key_id: str


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class JwtError(Exception):
    """Base for everything ``api.auth.jwt`` raises."""


class InvalidTokenError(JwtError):
    """Signature mismatch, malformed structure, missing claim, or wrong type."""


class ExpiredTokenError(JwtError):
    """``exp`` is in the past. Distinguished from ``InvalidTokenError`` so
    the route handler can return 401 with a slightly more useful body."""


class ReusedRefreshTokenError(JwtError):
    """Refresh JWT was already rotated.

    Raised by ``rotate`` AFTER the user's entire refresh chain has been
    revoked. Callers don't need to perform any further cleanup — the
    side effect has happened before the exception arrives. ``user_id``
    is attached for incident-response logging.
    """

    def __init__(self, user_id: UUID) -> None:
        super().__init__("refresh token already rotated; chain revoked")
        self.user_id = user_id


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _secret() -> str:
    """Active signing secret. Fails loudly if unset (CLAUDE.md backend rules)."""
    secret = get_settings().jwt_secret
    if not secret:
        raise RuntimeError(
            "JWT_SECRET is empty — refusing to issue or decode tokens with no signing key"
        )
    return secret


def _now() -> datetime:
    # Wrapped so tests can monkeypatch a fixed clock if needed; pyjwt
    # reads the system clock for ``exp`` validation, so we only do the
    # same for ``iat``.
    return datetime.now(UTC)


def _hash_refresh(token: str) -> str:
    """SHA-256 of the JWS string, hex-encoded. Used as ``token_hash`` in DB."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _encode(user_id: UUID, token_type: TokenType, ttl_seconds: int) -> str:
    """Build + sign a JWT. Internal — callers go through ``issue_pair``."""
    issued_at = _now()
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "typ": token_type.value,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        # Unique-per-issue — protects against the (vanishingly unlikely)
        # collision of two refresh JWTs hashing to the same value.
        "jti": uuid4().hex,
    }
    return pyjwt.encode(payload, _secret(), algorithm=ALGORITHM, headers={"kid": KEY_ID})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def decode(token: str) -> Claims:
    """Verify signature + expiry + claim presence; return parsed claims.

    Raises ``ExpiredTokenError`` for an ``exp`` in the past, or
    ``InvalidTokenError`` for any other failure (bad signature, missing
    required claim, malformed structure, wrong algorithm in header).
    Callers map both to 401 with no info-leak on which mode hit.
    """
    try:
        payload = pyjwt.decode(
            token,
            _secret(),
            algorithms=[ALGORITHM],
            options={"require": ["exp", "iat", "sub", "typ", "jti"]},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise ExpiredTokenError(str(exc)) from exc
    except pyjwt.InvalidTokenError as exc:
        # Catches: InvalidSignatureError, DecodeError, MissingRequiredClaimError, …
        raise InvalidTokenError(str(exc)) from exc

    try:
        header = pyjwt.get_unverified_header(token)
    except pyjwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    try:
        token_type = TokenType(payload["typ"])
    except ValueError as exc:
        raise InvalidTokenError(f"unknown token type {payload['typ']!r}") from exc

    return Claims(
        user_id=UUID(payload["sub"]),
        token_type=token_type,
        issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        jti=payload["jti"],
        key_id=str(header.get("kid", "")),
    )


async def issue_pair(user_id: UUID, repo: RefreshTokenRepository) -> tuple[str, str]:
    """Issue ``(access, refresh)``. Persists the refresh hash in DB.

    Caller wraps in ``transaction(session)`` so the refresh-row insert
    commits atomically with whatever else happened in the same request
    (the user-row insert for signup, the chain-revoke for rotate).
    """
    settings = get_settings()
    access = _encode(user_id, TokenType.ACCESS, settings.access_token_ttl)
    refresh = _encode(user_id, TokenType.REFRESH, settings.refresh_token_ttl)
    await repo.create(token_hash=_hash_refresh(refresh), user_id=user_id)
    return access, refresh


async def rotate(refresh_token: str, repo: RefreshTokenRepository) -> tuple[str, str]:
    """Single-use refresh rotation.

    Happy path: verifies the refresh JWT, marks its row revoked, and
    issues a fresh ``(access, refresh)`` pair.

    Replay path: if the presented token's row is already revoked, the
    user's entire chain is wiped via ``revoke_all_for_user`` and
    ``ReusedRefreshTokenError`` is raised. The chain wipe happens
    BEFORE the exception leaves the function, so the route handler
    doesn't need to remember to do follow-up cleanup.

    Raises:
        ExpiredTokenError: refresh JWT's ``exp`` has passed.
        InvalidTokenError: signature invalid, malformed, or wrong type
            (caller passed an access JWT, etc.).
        ReusedRefreshTokenError: already rotated; chain revoked as a
            side effect.
    """
    claims = decode(refresh_token)
    if claims.token_type is not TokenType.REFRESH:
        raise InvalidTokenError(f"expected refresh token, got {claims.token_type.value}")

    token_hash = _hash_refresh(refresh_token)
    row = await repo.get_by_hash(token_hash)
    if row is None:
        # JWT signature is valid against our active secret but no DB row
        # exists. Could be replay after retention cleanup, or a token
        # forged against a leaked-but-not-rotated secret. Either way:
        # not trusted.
        raise InvalidTokenError("refresh token not recognized")

    if row.revoked_at is not None:
        await repo.revoke_all_for_user(row.user_id)
        raise ReusedRefreshTokenError(row.user_id)

    await repo.mark_revoked(row.id)
    return await issue_pair(row.user_id, repo)
