"""Tests for ``api.auth.jwt``.

The ticket's four failure-mode requirements:

* expired access token  → ``ExpiredTokenError``
* expired refresh token → ``ExpiredTokenError``
* tampered token        → ``InvalidTokenError``
* reused refresh        → ``ReusedRefreshTokenError`` AND chain revoked

Plus the happy path (issue → decode → rotate → new pair).

``JWT_SECRET`` is injected via an autouse monkeypatch fixture; the
module's ``_secret()`` re-reads on every call so this works without
restarting the process.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from _factories import make_user
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import jwt as auth_jwt
from api.auth.jwt import (
    ExpiredTokenError,
    InvalidTokenError,
    ReusedRefreshTokenError,
    TokenType,
    decode,
    issue_pair,
    rotate,
)
from api.repositories.refresh_tokens import RefreshTokenRepository


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Non-empty so ``_secret()`` doesn't raise; value is irrelevant past
    # that. Set via env var so the same ``Settings`` resolution path the
    # app uses in production exercises here too.
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-deploy-anywhere")
    yield


# ---------------------------------------------------------------------------
# Issue / decode happy paths.
# ---------------------------------------------------------------------------


async def test_issue_pair_returns_distinct_access_and_refresh(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session)
    repo = RefreshTokenRepository(db_session)

    access, refresh = await issue_pair(user.id, repo)
    assert access != refresh

    a = decode(access)
    r = decode(refresh)

    assert a.token_type is TokenType.ACCESS
    assert r.token_type is TokenType.REFRESH
    assert a.user_id == user.id
    assert r.user_id == user.id
    # ``jti`` is unique per-issue — the only way two tokens issued at
    # the same instant can be distinguished if everything else matches.
    assert a.jti != r.jti


async def test_issue_pair_persists_refresh_hash(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session)
    repo = RefreshTokenRepository(db_session)

    _, refresh = await issue_pair(user.id, repo)
    row = await repo.get_by_hash(auth_jwt._hash_refresh(refresh))
    assert row is not None
    assert row.user_id == user.id
    assert row.revoked_at is None


def test_decode_returns_kid_from_header() -> None:
    # Key rotation hook (module docstring) — every issued token carries
    # the active KEY_ID in its header. The decode path surfaces it on
    # the Claims object.
    tok = auth_jwt._encode(uuid4(), TokenType.ACCESS, ttl_seconds=60)
    claims = decode(tok)
    assert claims.key_id == auth_jwt.KEY_ID


# ---------------------------------------------------------------------------
# Failure modes — the four required by the ticket.
# ---------------------------------------------------------------------------


def test_decode_raises_on_expired_access() -> None:
    # Negative TTL → exp in the past — exercises pyjwt's
    # ExpiredSignatureError path mapped to our ExpiredTokenError.
    tok = auth_jwt._encode(uuid4(), TokenType.ACCESS, ttl_seconds=-1)
    with pytest.raises(ExpiredTokenError):
        decode(tok)


def test_decode_raises_on_expired_refresh() -> None:
    tok = auth_jwt._encode(uuid4(), TokenType.REFRESH, ttl_seconds=-1)
    with pytest.raises(ExpiredTokenError):
        decode(tok)


def test_decode_raises_on_tampered_signature() -> None:
    tok = auth_jwt._encode(uuid4(), TokenType.ACCESS, ttl_seconds=60)
    # Flip the FIRST character of the signature segment, not the last. A
    # 32-byte HMAC-SHA256 signature is 43 base64url chars, and the final
    # char encodes only the top 4 bits of the last byte — its low 2 bits
    # are padding the decoder discards. So editing the last char decodes
    # to identical signature bytes whenever it is already "A", the HMAC
    # still verifies, and this test spuriously passes ~1 run in 16. The
    # first char carries 6 significant bits, so changing it always
    # perturbs the signature. Stays in the base64url alphabet so we fail
    # on signature validation rather than on decode.
    header, payload, signature = tok.split(".")
    tampered_sig = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(InvalidTokenError):
        decode(f"{header}.{payload}.{tampered_sig}")


def test_decode_raises_on_wrong_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tok = auth_jwt._encode(uuid4(), TokenType.ACCESS, ttl_seconds=60)
    # Rotate the secret out from under a previously-issued token.
    # Length ≥32 bytes — pyjwt's HS256 emits an InsecureKeyLengthWarning
    # otherwise, which would clutter the test output without flagging a
    # real bug.
    monkeypatch.setenv("JWT_SECRET", "a-completely-different-32+-byte-secret")
    with pytest.raises(InvalidTokenError):
        decode(tok)


def test_decode_raises_when_jwt_secret_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "")
    # Refuse to operate without a key — defense against a misconfigured
    # production deploy.
    with pytest.raises(RuntimeError, match="JWT_SECRET is empty"):
        decode("anything.at.all")


# ---------------------------------------------------------------------------
# Rotate.
# ---------------------------------------------------------------------------


async def test_rotate_issues_new_pair_and_marks_old_revoked(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session)
    repo = RefreshTokenRepository(db_session)

    _, refresh = await issue_pair(user.id, repo)
    old_hash = auth_jwt._hash_refresh(refresh)

    new_access, new_refresh = await rotate(refresh, repo)
    assert new_refresh != refresh
    assert new_access != ""  # smoke check

    # Old row revoked, new row inserted active.
    old_row = await repo.get_by_hash(old_hash)
    assert old_row is not None
    assert old_row.revoked_at is not None

    new_row = await repo.get_by_hash(auth_jwt._hash_refresh(new_refresh))
    assert new_row is not None
    assert new_row.revoked_at is None


async def test_rotate_rejects_access_token(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    repo = RefreshTokenRepository(db_session)

    access, _ = await issue_pair(user.id, repo)
    # Access tokens are the wrong type for rotation — the route handler
    # should never reach this path, but defense in depth.
    with pytest.raises(InvalidTokenError, match="expected refresh"):
        await rotate(access, repo)


async def test_rotate_raises_on_unknown_refresh(
    db_session: AsyncSession,
) -> None:
    # Well-formed JWT signed by our secret, but no DB row — replay
    # after cleanup, or a token issued before a key rotation.
    tok = auth_jwt._encode(uuid4(), TokenType.REFRESH, ttl_seconds=3600)
    repo = RefreshTokenRepository(db_session)
    with pytest.raises(InvalidTokenError, match="not recognized"):
        await rotate(tok, repo)


async def test_reused_refresh_revokes_entire_chain(
    db_session: AsyncSession,
) -> None:
    # TKT-P1-06 acceptance criterion.
    user = await make_user(db_session)
    repo = RefreshTokenRepository(db_session)

    _, refresh = await issue_pair(user.id, repo)

    # First rotation succeeds. The chain now holds an active token
    # (``new_refresh``) and the now-revoked original ``refresh``.
    _, new_refresh = await rotate(refresh, repo)
    new_hash = auth_jwt._hash_refresh(new_refresh)

    # Replaying the original — already revoked — must:
    # (a) raise ReusedRefreshTokenError with the right user_id, and
    # (b) revoke every still-active refresh token for that user before
    #     the exception leaves the function.
    with pytest.raises(ReusedRefreshTokenError) as exc_info:
        await rotate(refresh, repo)
    assert exc_info.value.user_id == user.id

    surviving = await repo.get_by_hash(new_hash)
    assert surviving is not None
    assert surviving.revoked_at is not None, "rotation chain must be fully revoked after a replay"

    # And the now-revoked chain stays revoked under a second replay
    # (revoke_all_for_user is idempotent on already-revoked rows).
    with pytest.raises(ReusedRefreshTokenError):
        await rotate(refresh, repo)
