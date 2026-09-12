"""In-memory rate limiting for ``/auth/*`` — TKT-P1-07.

Why this exists
---------------
The auth endpoints are the app's only unauthenticated write surface.
Without a limiter, ``POST /auth/login`` is an online password-guessing
oracle and ``POST /auth/signup`` is an unbounded row-creation primitive
that also burns ~75 ms of argon2 CPU per call.

Scope and its limits
--------------------
Phase 1 ships a per-process, in-memory token bucket, per the ticket
("in-memory bucket is fine in Phase 1; Redis-backed deferred to Phase 8").
Two consequences worth stating plainly, because they are the reason the
Phase 8 replacement exists:

* **Per-process.** N API workers means N × the configured budget. Fine at
  Phase 1's single-process scale; wrong the moment the API is replicated.
* **Per-client-IP.** A NAT'd office shares one bucket; a distributed
  attacker gets one bucket per source address. Capacity is set high enough
  that shared-IP false positives are unlikely and low enough that a single
  address cannot mount a meaningful guessing campaign.

Redis-backed replacement (Phase 8) keeps this module's interface and
swaps ``_TokenBucket``'s storage.

Why a token bucket
------------------
A fixed window lets a client spend its whole budget in the last instant of
one window and again in the first instant of the next — double the
intended burst across the boundary. A token bucket refills continuously,
so the sustained rate holds no matter where requests land in time.

Why raw ASGI rather than ``BaseHTTPMiddleware``
-----------------------------------------------
``BaseHTTPMiddleware`` wraps every request in an anyio task group and
proxies the response body through a stream. This middleware is installed
app-wide but only acts on ``/auth/*``, so that overhead would land on the
share-sheet hot path (PRD §9.2) for no benefit. The raw-ASGI form
short-circuits to ``await self.app(...)`` on the first character
comparison for every other route.

Concurrency
-----------
Bucket reads and writes happen in one synchronous stretch with no ``await``
between them, so the event loop cannot interleave two requests mid-update.
No lock is needed, and adding one would only serialize the loop.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Default budget for ``/auth/*``, overridable via ``Settings``. 10 requests
# of burst, refilling at 10/minute: enough for a user fumbling their
# password on a shared office IP, far too slow for credential stuffing.
DEFAULT_CAPACITY = 10
DEFAULT_WINDOW_SECONDS = 60

# Prune idle buckets once the table passes this size, so a stream of
# spoofed source addresses cannot grow the dict without bound. Well above
# any plausible Phase 1 concurrent-client count, so pruning is rare.
_PRUNE_THRESHOLD = 10_000


@dataclass
class _Bucket:
    """One client's token allowance. ``tokens`` is fractional — refill is
    continuous, not stepped."""

    tokens: float
    updated_at: float


class TokenBucketLimiter:
    """Fixed-capacity, continuously-refilling buckets keyed by an opaque
    string.

    ``capacity`` tokens, refilled over ``window_seconds``; one token per
    request. Exposed separately from the middleware so it can be unit
    tested against a fake clock, and so the Phase 8 Redis version can be
    dropped in behind the same two methods.
    """

    def __init__(self, capacity: int, window_seconds: float) -> None:
        if capacity < 0:
            raise ValueError("capacity must be non-negative")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._capacity = float(capacity)
        self._window_seconds = float(window_seconds)
        self._refill_per_second = self._capacity / self._window_seconds
        self._buckets: dict[str, _Bucket] = {}

    def acquire(self, key: str, *, now: float | None = None) -> float | None:
        """Spend one token for ``key``.

        Returns ``None`` when the request is allowed, or the number of
        seconds until the next token is available when it is not. A
        rejected request spends nothing, so a client that keeps hammering
        does not push its own recovery further out.
        """
        now = time.monotonic() if now is None else now

        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self._capacity, updated_at=now)
            self._buckets[key] = bucket
            if len(self._buckets) > _PRUNE_THRESHOLD:
                self._prune(now)
        else:
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_per_second)
            bucket.updated_at = now

        if bucket.tokens < 1.0:
            if self._refill_per_second == 0.0:
                # ``capacity=0`` closes the endpoint entirely. No token
                # will ever accrue, so there is no honest wait to report;
                # hand back the window as a "come back later" hint rather
                # than dividing by zero or promising infinity.
                return self._window_seconds
            return (1.0 - bucket.tokens) / self._refill_per_second

        bucket.tokens -= 1.0
        return None

    def _prune(self, now: float) -> None:
        """Drop buckets that have refilled to capacity.

        A full bucket is indistinguishable from a bucket that has never
        been used, so forgetting it loses no rate-limiting state.
        """
        full_after = self._window_seconds
        self._buckets = {
            key: bucket
            for key, bucket in self._buckets.items()
            if now - bucket.updated_at < full_after
        }

    def reset(self) -> None:
        """Forget all buckets. Test-support only."""
        self._buckets.clear()


class RateLimitMiddleware:
    """Applies ``limiter`` to requests whose path starts with ``path_prefix``.

    Rejections are a bare 429 with ``Retry-After``. The body carries no
    detail about which bucket was hit or how much budget remains —
    a limiter that reports its own state is a limiter that can be mapped.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: TokenBucketLimiter,
        path_prefix: str = "/auth/",
        known_paths: Iterable[str] = (),
    ) -> None:
        self.app = app
        self._limiter = limiter
        self._path_prefix = path_prefix
        # Bucket keys are per-endpoint, and this is the set of endpoints that
        # actually exist. Anything else collapses to one shared key.
        self._known_paths = frozenset(known_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self._path_prefix):
            await self.app(scope, receive, send)
            return

        retry_after = self._limiter.acquire(self._key(scope))
        if retry_after is None:
            await self.app(scope, receive, send)
            return

        await _send_429(send, retry_after)

    def _key(self, scope: Scope) -> str:
        """Bucket key: client address + path.

        Per-path rather than per-prefix so exhausting the login budget
        does not also block a legitimate refresh — the endpoints have
        genuinely different abuse profiles.

        Only *known* endpoint paths get their own bucket. The middleware
        runs before routing, so an unrecognized path under the prefix is a
        404 that never reaches a handler — but it used to mint a bucket
        anyway, which handed one client an unbounded key space: 5,000
        requests to ``/auth/x1``…``/auth/x5000`` created 5,000 permanent
        dict entries from a single address, no spoofing required. Folding
        them into one ``<unrouted>`` key caps the table at (clients ×
        endpoints) while keeping the per-endpoint separation that stops an
        exhausted login bucket from also blocking refresh.

        ``scope["client"]`` is the peer address. ``X-Forwarded-For`` is
        deliberately ignored: honoring it without a trusted-proxy
        allowlist makes the limiter opt-out via one spoofed header. Note
        the flip side, which is a deployment gate rather than a Phase 8
        nicety: behind a load balancer every request shares the proxy's
        address, so the per-IP budget becomes a global one and a single
        attacker can 429 every user. Add the trusted-proxy allowlist
        before the API goes behind an ingress.
        """
        client = scope.get("client")
        host = client[0] if client else "unknown"
        path = scope["path"] if scope["path"] in self._known_paths else "<unrouted>"
        return f"{host}:{path}"


async def _send_429(send: Send, retry_after: float) -> None:
    body = b'{"detail":"Too many requests. Please retry later."}'
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        # Whole seconds, rounded up and floored at 1: RFC 9110 delay-seconds
        # is an integer, and a "0" would invite an immediate retry that is
        # guaranteed to fail again.
        (b"retry-after", str(max(1, math.ceil(retry_after))).encode()),
    ]
    start: Message = {"type": "http.response.start", "status": 429, "headers": headers}
    await send(start)
    await send({"type": "http.response.body", "body": body})
