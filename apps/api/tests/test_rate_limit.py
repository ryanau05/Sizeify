"""Unit tests for the ``/auth/*`` token bucket — TKT-P1-07.

The bucket is driven with an explicit ``now`` so refill behavior is
tested against arithmetic rather than against ``time.monotonic`` and a
``sleep``. Middleware wiring is covered by the 429 tests in
``test_routes_auth.py``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any
from uuid import UUID, uuid4

import pytest

from api import rate_limit
from api.auth import jwt as auth_jwt
from api.config import get_settings
from api.rate_limit import TokenBucketLimiter


def test_allows_up_to_capacity_then_rejects() -> None:
    limiter = TokenBucketLimiter(capacity=3, window_seconds=60)

    assert [limiter.acquire("k", now=0.0) for _ in range(3)] == [None, None, None]
    assert limiter.acquire("k", now=0.0) is not None


def test_rejection_reports_seconds_until_the_next_token() -> None:
    # 2 tokens / 10 s = one token every 5 s.
    limiter = TokenBucketLimiter(capacity=2, window_seconds=10)
    limiter.acquire("k", now=0.0)
    limiter.acquire("k", now=0.0)

    assert limiter.acquire("k", now=0.0) == pytest.approx(5.0)
    # Two seconds of refill later, three remain.
    assert limiter.acquire("k", now=2.0) == pytest.approx(3.0)


def test_refills_continuously() -> None:
    limiter = TokenBucketLimiter(capacity=2, window_seconds=10)
    limiter.acquire("k", now=0.0)
    limiter.acquire("k", now=0.0)
    assert limiter.acquire("k", now=4.9) is not None

    # One full token has accrued by t=5.
    assert limiter.acquire("k", now=5.0) is None
    assert limiter.acquire("k", now=5.0) is not None


def test_refill_is_capped_at_capacity() -> None:
    """An idle client gets its burst back, not an unbounded surplus."""
    limiter = TokenBucketLimiter(capacity=2, window_seconds=10)
    limiter.acquire("k", now=0.0)
    limiter.acquire("k", now=0.0)

    assert [limiter.acquire("k", now=10_000.0) for _ in range(2)] == [None, None]
    assert limiter.acquire("k", now=10_000.0) is not None


def test_rejected_requests_do_not_spend_tokens() -> None:
    """A client that keeps hammering must not push its own recovery out —
    otherwise a retry loop turns a brief throttle into a permanent one."""
    limiter = TokenBucketLimiter(capacity=1, window_seconds=10)
    limiter.acquire("k", now=0.0)
    for _ in range(50):
        limiter.acquire("k", now=1.0)

    assert limiter.acquire("k", now=10.0) is None


def test_keys_are_independent() -> None:
    limiter = TokenBucketLimiter(capacity=1, window_seconds=60)
    assert limiter.acquire("a", now=0.0) is None

    assert limiter.acquire("b", now=0.0) is None
    assert limiter.acquire("a", now=0.0) is not None


def test_zero_capacity_rejects_everything() -> None:
    """``capacity=0`` is the "close this endpoint" setting. It must report
    a finite retry hint rather than dividing by a zero refill rate."""
    limiter = TokenBucketLimiter(capacity=0, window_seconds=60)

    assert limiter.acquire("k", now=0.0) == 60.0
    assert limiter.acquire("k", now=10_000.0) == 60.0


@pytest.mark.parametrize(("capacity", "window"), [(-1, 60), (1, 0), (1, -5)])
def test_invalid_configuration_raises(capacity: int, window: float) -> None:
    with pytest.raises(ValueError):
        TokenBucketLimiter(capacity=capacity, window_seconds=window)


def test_reset_clears_state() -> None:
    limiter = TokenBucketLimiter(capacity=1, window_seconds=60)
    limiter.acquire("k", now=0.0)

    limiter.reset()

    assert limiter.acquire("k", now=0.0) is None


def test_pruning_keeps_buckets_that_are_still_throttled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bucket table is pruned so a stream of spoofed source addresses
    cannot grow it without bound. Pruning must never hand a client that is
    still inside its window a fresh budget."""
    monkeypatch.setattr(rate_limit, "_MAX_BUCKETS", 8)
    limiter = TokenBucketLimiter(capacity=1, window_seconds=60)
    assert limiter.acquire("hot", now=0.0) is None

    # Two new keys arrive while "hot" is still empty. Neither the age sweep
    # nor the cap may hand "hot" a fresh budget inside its window.
    limiter.acquire("new-1", now=5.0)
    limiter.acquire("new-2", now=5.0)

    assert limiter.acquire("hot", now=6.0) is not None


def test_pruning_drops_buckets_that_have_fully_refilled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full bucket is indistinguishable from one that never existed, so
    dropping it is what makes the table bounded without losing state."""
    monkeypatch.setattr(rate_limit, "_MAX_BUCKETS", 2)
    limiter = TokenBucketLimiter(capacity=1, window_seconds=60)
    limiter.acquire("stale-1", now=0.0)
    limiter.acquire("stale-2", now=0.0)

    limiter.acquire("fresh", now=600.0)

    # Only the key that tripped the prune is left; the two stale buckets
    # had refilled to capacity long before.
    assert list(limiter._buckets) == ["fresh"]


def test_unrouted_auth_paths_share_one_bucket() -> None:
    """The middleware runs before routing, so an unknown ``/auth/*`` path is a
    404 that never reaches a handler. It used to mint a bucket anyway, which
    handed one client an unbounded key space: 5,000 requests to distinct made-up
    paths created 5,000 permanent dict entries from a single address."""
    from api.rate_limit import RateLimitMiddleware

    limiter = TokenBucketLimiter(capacity=10, window_seconds=60)
    middleware = RateLimitMiddleware(
        app=None,  # type: ignore[arg-type]
        limiter=limiter,
        known_paths=["/auth/login", "/auth/signup", "/auth/refresh"],
    )

    for index in range(500):
        limiter.acquire(
            middleware._key(
                {"type": "http", "path": f"/auth/made-up-{index}", "client": ("1.2.3.4", 1)}
            )
        )
    for path in ("/auth/login", "/auth/signup", "/auth/refresh"):
        limiter.acquire(middleware._key({"type": "http", "path": path, "client": ("1.2.3.4", 1)}))

    # One shared <unrouted> bucket plus one per real endpoint.
    assert len(limiter._buckets) == 4


def test_known_endpoints_keep_separate_buckets() -> None:
    """Collapsing unrouted paths must not collapse the real ones — exhausting
    login should still leave refresh usable."""
    from api.rate_limit import RateLimitMiddleware

    limiter = TokenBucketLimiter(capacity=1, window_seconds=60)
    middleware = RateLimitMiddleware(
        app=None,  # type: ignore[arg-type]
        limiter=limiter,
        known_paths=["/auth/login", "/auth/refresh"],
    )
    scope = {"type": "http", "client": ("1.2.3.4", 1)}

    assert limiter.acquire(middleware._key({**scope, "path": "/auth/login"})) is None
    assert limiter.acquire(middleware._key({**scope, "path": "/auth/login"})) is not None
    assert limiter.acquire(middleware._key({**scope, "path": "/auth/refresh"})) is None


# ---------------------------------------------------------------------------
# Trusted-proxy resolution.
# ---------------------------------------------------------------------------


def _scope(peer: str, forwarded: str | None = None) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "type": "http",
        "path": "/auth/login",
        "client": (peer, 1),
        "headers": [],
    }
    if forwarded is not None:
        scope["headers"] = [(b"x-forwarded-for", forwarded.encode())]
    return scope


def _middleware(trusted: Sequence[str] = ()) -> Any:
    from api.rate_limit import RateLimitMiddleware

    return RateLimitMiddleware(
        app=None,  # type: ignore[arg-type]
        limiter=TokenBucketLimiter(capacity=10, window_seconds=60),
        known_paths=["/auth/login"],
        trusted_proxies=trusted,
    )


def test_forwarded_header_is_ignored_without_a_trusted_proxy() -> None:
    """The default. Honouring X-Forwarded-For unconditionally makes the
    limiter opt-out via one spoofed line."""
    assert _middleware()._client_ip(_scope("10.0.0.5", "1.2.3.4")) == "10.0.0.5"


def test_real_client_is_billed_behind_a_trusted_proxy() -> None:
    """Without this the whole deployment shares one bucket and a single
    attacker 429s every user — protection inverted, not degraded."""
    assert (
        _middleware(["10.0.0.0/8"])._client_ip(_scope("10.0.0.5", "203.0.113.9")) == "203.0.113.9"
    )


def test_client_forged_hops_are_not_believed() -> None:
    """X-Forwarded-For is append-only, so anything the client sent arrives on
    the left. Only entries our own infrastructure appended can be trusted."""
    middleware = _middleware(["10.0.0.0/8"])

    assert middleware._client_ip(_scope("10.0.0.5", "1.1.1.1, 203.0.113.9")) == "203.0.113.9"


def test_chained_trusted_hops_are_skipped() -> None:
    middleware = _middleware(["10.0.0.0/8"])

    assert middleware._client_ip(_scope("10.0.0.5", "203.0.113.9, 10.0.0.7")) == "203.0.113.9"


def test_untrusted_peer_keeps_its_own_address() -> None:
    middleware = _middleware(["10.0.0.0/8"])

    assert middleware._client_ip(_scope("198.51.100.2", "203.0.113.9")) == "198.51.100.2"


def test_all_hops_trusted_falls_back_to_the_peer() -> None:
    """Rather than believing the leftmost, fully client-controlled entry."""
    middleware = _middleware(["10.0.0.0/8"])

    assert middleware._client_ip(_scope("10.0.0.5", "10.0.0.7, 10.0.0.8")) == "10.0.0.5"


def test_trusted_proxy_without_a_forwarded_header_bills_the_peer() -> None:
    """A request that reaches us from a trusted proxy carrying no
    ``X-Forwarded-For`` at all — a health check, a probe, or an LB that has
    not been configured to append one.

    There is no client address to recover, so the peer is the only honest
    answer. It must not fall over on the empty header list either: the header
    walk is the one part of the limiter that runs before routing, so an
    exception here is a 500 on every request behind that proxy.
    """
    middleware = _middleware(["10.0.0.0/8"])

    assert middleware._client_ip(_scope("10.0.0.5")) == "10.0.0.5"


def test_unparseable_forwarded_entries_are_not_trusted() -> None:
    """Some proxies append the literal ``unknown`` when they cannot determine
    a peer. It parses as neither IPv4 nor IPv6, so it cannot be inside a
    trusted network — and treating a parse failure as "trusted, skip it"
    would let a client walk the header back to an entry it controls."""
    middleware = _middleware(["10.0.0.0/8"])

    assert middleware._client_ip(_scope("10.0.0.5", "203.0.113.9, unknown")) == "unknown"


def test_unparseable_trusted_cidr_does_not_widen_trust() -> None:
    """A typo in deployment config must not take the API down, and must not
    silently trust everything either."""
    middleware = _middleware(["not-a-cidr"])

    assert middleware._client_ip(_scope("10.0.0.5", "1.2.3.4")) == "10.0.0.5"


@pytest.fixture
def _rate_limit_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A signing secret for the tests that mint real tokens.

    ``_principal`` verifies signatures now, so a made-up token string is
    indistinguishable from a forged one and falls back to the peer address.
    These tests need tokens that actually decode.
    """
    monkeypatch.setenv("JWT_SECRET", "rate-limit-test-secret-do-not-deploy")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _access_token(user_id: UUID) -> str:
    """A signed access token for ``user_id``, as a real client would send."""
    return auth_jwt._encode(user_id, auth_jwt.TokenType.ACCESS, ttl_seconds=900)


def _bearer_scope(peer: str, token: str | None) -> dict[str, Any]:
    s = _scope(peer)
    s["path"] = "/closet/garments"
    if token:
        s["headers"] = [(b"authorization", f"Bearer {token}".encode())]
    return s


@pytest.mark.usefixtures("_rate_limit_jwt_secret")
def test_authenticated_surfaces_bill_the_credential_not_the_address() -> None:
    """Two users behind one NAT must not share a budget, and one user must not
    escape theirs by changing networks."""
    middleware = _middleware()
    middleware._key_by_bearer = True

    alice, bob = uuid4(), uuid4()
    # Two tokens for the same user: distinct strings, same subject. Under the
    # old raw-token hashing these were two buckets.
    alice_token_1, alice_token_2 = _access_token(alice), _access_token(alice)

    same_user_two_networks = {
        middleware._principal(_bearer_scope("10.0.0.1", alice_token_1)),
        middleware._principal(_bearer_scope("203.0.113.7", alice_token_1)),
    }
    two_users_one_network = {
        middleware._principal(_bearer_scope("10.0.0.1", alice_token_1)),
        middleware._principal(_bearer_scope("10.0.0.1", _access_token(bob))),
    }
    same_user_two_tokens = {
        middleware._principal(_bearer_scope("10.0.0.1", alice_token_1)),
        middleware._principal(_bearer_scope("10.0.0.1", alice_token_2)),
    }

    assert len(same_user_two_networks) == 1, "same credential should share a bucket"
    assert len(two_users_one_network) == 2, "different credentials should not"
    assert len(same_user_two_tokens) == 1, "one user's two tokens are one budget"


@pytest.mark.usefixtures("_rate_limit_jwt_secret")
def test_a_forged_token_cannot_mint_its_own_bucket() -> None:
    """The bypass this keying exists to close.

    ``_principal`` used to hash the raw Authorization header, so the bucket
    key was chosen by the caller: send a different token each time and every
    request is a fresh bucket starting at full capacity. Measured before the
    fix, 50 requests from one address with a rotating forged token were
    throttled 0 times and created 50 buckets. No account required — the
    middleware runs before routing, so the 401 comes too late to matter.

    Unsigned garbage must therefore fall back to the peer address, which is
    bounded, rather than to anything the sender controls.
    """
    middleware = _middleware()
    middleware._key_by_bearer = True

    forged = {middleware._principal(_bearer_scope("10.0.0.1", f"forged-{i}")) for i in range(50)}

    assert forged == {"10.0.0.1"}, "a forged token must bill the peer, not mint a key"


@pytest.mark.usefixtures("_rate_limit_jwt_secret")
def test_an_expired_token_bills_the_address_not_a_fresh_bucket() -> None:
    """Expiry is the forgery case that arrives without an attacker.

    A client looping on a stale token would otherwise hold a full private
    bucket forever, because the subject still parses.
    """
    middleware = _middleware()
    middleware._key_by_bearer = True
    expired = auth_jwt._encode(uuid4(), auth_jwt.TokenType.ACCESS, ttl_seconds=-1)

    assert middleware._principal(_bearer_scope("10.0.0.1", expired)) == "10.0.0.1"


@pytest.mark.usefixtures("_rate_limit_jwt_secret")
def test_the_bucket_key_never_contains_the_raw_token() -> None:
    """Keys end up in memory dumps and, if ever logged, in the log store."""
    middleware = _middleware()
    middleware._key_by_bearer = True
    token = _access_token(uuid4())

    assert token not in middleware._principal(_bearer_scope("10.0.0.1", token))


def test_unauthenticated_request_to_an_authenticated_surface_bills_the_address() -> None:
    """It will 401, but a flood of them still has to be bounded."""
    middleware = _middleware()
    middleware._key_by_bearer = True
    s = _scope("198.51.100.4")
    s["path"] = "/closet/garments"

    assert middleware._principal(s) == "198.51.100.4"


def test_a_full_table_never_resets_an_existing_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overflow must not hand a throttled client a fresh allowance.

    The table was previously kept under its cap by evicting in insertion
    order. A bucket's content is spent budget, so evicting one restores that
    key to full capacity — which made eviction a rate-limit reset that the
    throttled client could trigger itself, simply by minting enough new keys
    to overflow the table. Measured against that version: a client with zero
    tokens left was served again immediately after the flood.
    """
    monkeypatch.setattr(rate_limit, "_MAX_BUCKETS", 50)
    limiter = TokenBucketLimiter(capacity=3, window_seconds=600)
    now = 1000.0

    for _ in range(3):
        assert limiter.acquire("spender", now=now) is None
    assert limiter.acquire("spender", now=now) is not None, "should be out of tokens"

    # Flood the table well past the cap, inside the window so nothing is idle
    # enough for the age sweep to reclaim.
    for i in range(60):
        limiter.acquire(f"filler-{i}", now=now)

    assert "spender" in limiter._buckets, "a live bucket was dropped"
    assert limiter.acquire("spender", now=now) is not None, (
        "flooding the table reset an exhausted budget"
    )


def test_overflow_keys_share_one_budget_and_the_table_stays_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the cap, new arrivals share a bucket rather than growing the dict.

    Sharing is the intended degradation: during a flood the new keys *are* the
    flood, so throttling them against each other is correct. The ceiling holds
    no matter how fast keys arrive.
    """
    monkeypatch.setattr(rate_limit, "_MAX_BUCKETS", 20)
    limiter = TokenBucketLimiter(capacity=5, window_seconds=600)
    now = 1000.0

    outcomes = [limiter.acquire(f"key-{i}", now=now) for i in range(200)]

    assert len(limiter._buckets) <= 20 + 1, "table grew past the cap"
    assert rate_limit._OVERFLOW_KEY in limiter._buckets
    assert any(o is not None for o in outcomes), "overflow arrivals were never throttled"
