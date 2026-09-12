"""Unit tests for the ``/auth/*`` token bucket — TKT-P1-07.

The bucket is driven with an explicit ``now`` so refill behavior is
tested against arithmetic rather than against ``time.monotonic`` and a
``sleep``. Middleware wiring is covered by the 429 tests in
``test_routes_auth.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from api import rate_limit
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
    monkeypatch.setattr(rate_limit, "_PRUNE_THRESHOLD", 2)
    limiter = TokenBucketLimiter(capacity=1, window_seconds=60)
    assert limiter.acquire("hot", now=0.0) is None

    # Two new keys arrive and trip a prune while "hot" is still empty.
    limiter.acquire("new-1", now=5.0)
    limiter.acquire("new-2", now=5.0)

    assert limiter.acquire("hot", now=6.0) is not None


def test_pruning_drops_buckets_that_have_fully_refilled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full bucket is indistinguishable from one that never existed, so
    dropping it is what makes the table bounded without losing state."""
    monkeypatch.setattr(rate_limit, "_PRUNE_THRESHOLD", 2)
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


def test_unparseable_trusted_cidr_does_not_widen_trust() -> None:
    """A typo in deployment config must not take the API down, and must not
    silently trust everything either."""
    middleware = _middleware(["not-a-cidr"])

    assert middleware._client_ip(_scope("10.0.0.5", "1.2.3.4")) == "10.0.0.5"


def test_authenticated_surfaces_bill_the_credential_not_the_address() -> None:
    """Two users behind one NAT must not share a budget, and one user must not
    escape theirs by changing networks."""
    middleware = _middleware()
    middleware._key_by_bearer = True

    def scope(peer: str, token: str | None) -> dict[str, Any]:
        s = _scope(peer)
        s["path"] = "/closet/garments"
        if token:
            s["headers"] = [(b"authorization", f"Bearer {token}".encode())]
        return s

    same_user_two_networks = {
        middleware._principal(scope("10.0.0.1", "tok-a")),
        middleware._principal(scope("203.0.113.7", "tok-a")),
    }
    two_users_one_network = {
        middleware._principal(scope("10.0.0.1", "tok-a")),
        middleware._principal(scope("10.0.0.1", "tok-b")),
    }

    assert len(same_user_two_networks) == 1, "same credential should share a bucket"
    assert len(two_users_one_network) == 2, "different credentials should not"


def test_the_bucket_key_never_contains_the_raw_token() -> None:
    """Keys end up in memory dumps and, if ever logged, in the log store."""
    middleware = _middleware()
    middleware._key_by_bearer = True
    s = _scope("10.0.0.1")
    s["path"] = "/closet/garments"
    s["headers"] = [(b"authorization", b"Bearer super-secret-token-value")]

    assert "super-secret-token-value" not in middleware._principal(s)


def test_unauthenticated_request_to_an_authenticated_surface_bills_the_address() -> None:
    """It will 401, but a flood of them still has to be bounded."""
    middleware = _middleware()
    middleware._key_by_bearer = True
    s = _scope("198.51.100.4")
    s["path"] = "/closet/garments"

    assert middleware._principal(s) == "198.51.100.4"
