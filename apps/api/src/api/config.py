from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings.

    Values resolve in this order: process env vars > `.env` file > defaults
    declared here. Field names are matched case-insensitively against env vars
    (e.g. ``database_url`` is populated from ``DATABASE_URL``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://sizeify:sizeify@localhost:5432/sizeify"


def get_settings() -> Settings:
    return Settings()
