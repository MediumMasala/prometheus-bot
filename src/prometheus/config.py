from __future__ import annotations

from typing import Literal

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: str
    owner_telegram_user_id: int | None = None

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    database_url: str

    webhook_url: str = ""
    webhook_secret: str = ""
    port: int = 8080
    # Render auto-injects RENDER_EXTERNAL_URL on web services. We fall back to
    # this so first deploy works without a manual WEBHOOK_URL set.
    render_external_url: str = ""

    env: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Render's connectionString is plain postgresql://; we want async driver.
        if v.startswith("postgresql+"):
            return v
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_webhook_url(self) -> str:
        return self.webhook_url or self.render_external_url

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Sync URL for APScheduler's SQLAlchemyJobStore."""
        url = self.database_url
        if "+asyncpg" in url:
            return url.replace("+asyncpg", "")
        if "+psycopg" in url and "+psycopg2" not in url:
            return url.replace("+psycopg", "")
        return url


settings = Settings()
