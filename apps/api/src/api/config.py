from pydantic_settings import BaseSettings, SettingsConfigDict


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
        env_file=".env",
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


def get_settings() -> Settings:
    return Settings()
