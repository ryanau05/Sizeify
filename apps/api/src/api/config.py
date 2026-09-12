from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from api import rate_limit

#: ``apps/api/.env``, resolved from this file rather than the process CWD.
#: A relative ``env_file`` made the loaded values depend on where the
#: interpreter was started: ``uv run pytest`` from the repo root and from
#: ``apps/api`` resolved different files.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Application settings.

    Values resolve in this order: process env vars > `.env` file > defaults
    declared here. Field names are matched case-insensitively against env vars
    (e.g. ``database_url`` is populated from ``DATABASE_URL``).

    Secrets (``anthropic_api_key``, ``jwt_secret``) default to empty so the
    process boots in local dev without a populated ``.env``; downstream code
    (auth, LLM client) is expected to fail loudly if it tries to use an empty
    secret in a non-dev environment.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://sizeify:sizeify@localhost:5432/sizeify"
    redis_url: str = "redis://localhost:6379/0"

    anthropic_api_key: str = ""
    jwt_secret: str = ""

    # TTLs in seconds. Plain ints so env vars are plain integers
    # (pydantic v2 ``timedelta`` from env requires ISO-8601, which is friction
    # we don't need); the JWT issuer wraps these as ``timedelta`` at the boundary.
    access_token_ttl: int = 15 * 60
    refresh_token_ttl: int = 30 * 24 * 60 * 60

    # ``/auth/*`` rate limit (TKT-P1-07): ``auth_rate_limit_capacity``
    # requests of burst per client IP per endpoint, refilling over
    # ``auth_rate_limit_window_seconds``. Surfaced as settings so the
    # deployment can tighten them without a code change, and so tests can
    # exercise the 429 path without issuing the production budget's worth
    # of requests. See ``api.rate_limit`` for the per-process caveat.
    auth_rate_limit_capacity: int = rate_limit.DEFAULT_CAPACITY
    auth_rate_limit_window_seconds: int = rate_limit.DEFAULT_WINDOW_SECONDS

    # Budget for the authenticated surfaces (/closet, /me), per credential
    # rather than per address. Looser than the auth budget because these are
    # ordinary app traffic, but bounded: POST /closet/garments creates rows,
    # GET /closet/fit-profile recomputes the whole profile, and GET /me/export
    # runs three unbounded queries by design.
    user_rate_limit_capacity: int = 120
    user_rate_limit_window_seconds: int = 60

    #: Ceiling on how many live garments one user may hold. v1 onboarding asks
    #: for 3-5 (PRD §10.1); this is far above any real closet and exists so the
    #: create endpoint is not an unbounded row-creation primitive.
    max_closet_garments: int = 500

    # Comma-separated CIDRs of load balancers / ingress allowed to set
    # ``X-Forwarded-For``. Empty by default, which means the rate limiter
    # trusts nothing and keys on the TCP peer.
    #
    # This has to be set before the API goes behind a proxy. Unset, every
    # request arrives with the proxy's address, the whole deployment shares
    # one bucket, and a single attacker 429s every user — protection inverts
    # into a denial of service rather than merely degrading.
    trusted_proxy_cidrs: str = ""

    def trusted_proxies(self) -> tuple[str, ...]:
        """``trusted_proxy_cidrs`` split into individual networks."""
        return tuple(part.strip() for part in self.trusted_proxy_cidrs.split(",") if part.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read once.

    Cached to match ``repositories.base.get_engine``. Uncached, every call
    re-read and re-parsed the dotenv file from disk — and ``auth.jwt._secret``
    calls it on every token encode *and* decode, so an authenticated request
    paid blocking file I/O on the event loop just to look up the signing key
    (measured 0.548 ms/call).

    Tests that vary the environment call ``get_settings.cache_clear()``; the
    autouse fixture in ``tests/conftest.py`` does it around every test.
    """
    return Settings()
