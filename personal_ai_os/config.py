from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_base_url: str = Field(default="http://localhost:8000", validation_alias="APP_BASE_URL")

    database_url: str = Field(validation_alias="DATABASE_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")

    telegram_bot_token: str = Field(validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_webhook_secret: str = Field(validation_alias="TELEGRAM_WEBHOOK_SECRET")

    anthropic_api_key: str = Field(validation_alias="ANTHROPIC_API_KEY")
    anthropic_model_sonnet: str = Field(
        default="claude-sonnet-4-5-20250929", validation_alias="ANTHROPIC_MODEL_SONNET"
    )
    anthropic_model_haiku: str = Field(
        default="claude-haiku-4-5-20251001", validation_alias="ANTHROPIC_MODEL_HAIKU"
    )

    tavily_api_key: str = Field(default="", validation_alias="TAVILY_API_KEY")

    oauth_encryption_key_hex: str = Field(validation_alias="OAUTH_ENCRYPTION_KEY")

    google_client_id: str = Field(default="", validation_alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", validation_alias="GOOGLE_CLIENT_SECRET")

    jira_client_id: str = Field(default="", validation_alias="JIRA_CLIENT_ID")
    jira_client_secret: str = Field(default="", validation_alias="JIRA_CLIENT_SECRET")

    paddle_webhook_secret: str = Field(default="", validation_alias="PADDLE_WEBHOOK_SECRET")
    paddle_price_personal: str = Field(default="", validation_alias="PADDLE_PRICE_PERSONAL")
    paddle_price_pro: str = Field(default="", validation_alias="PADDLE_PRICE_PRO")
    paddle_price_business: str = Field(default="", validation_alias="PADDLE_PRICE_BUSINESS")
    paddle_price_pkg_s: str = Field(default="", validation_alias="PADDLE_PRICE_PKG_S")
    paddle_price_pkg_m: str = Field(default="", validation_alias="PADDLE_PRICE_PKG_M")
    paddle_price_pkg_l: str = Field(default="", validation_alias="PADDLE_PRICE_PKG_L")

    checkout_url_personal: str = Field(default="", validation_alias="CHECKOUT_URL_PERSONAL")
    checkout_url_pro: str = Field(default="", validation_alias="CHECKOUT_URL_PRO")
    checkout_url_business: str = Field(default="", validation_alias="CHECKOUT_URL_BUSINESS")
    checkout_url_pkg_s: str = Field(default="", validation_alias="CHECKOUT_URL_PKG_S")
    checkout_url_pkg_m: str = Field(default="", validation_alias="CHECKOUT_URL_PKG_M")
    checkout_url_pkg_l: str = Field(default="", validation_alias="CHECKOUT_URL_PKG_L")

    sentry_dsn: str = Field(default="", validation_alias="SENTRY_DSN")

    rate_limit_per_minute: int = Field(default=30, validation_alias="RATE_LIMIT_PER_MINUTE")

    # Testing: no token cap, unlimited custom agents, no per-minute rate limit.
    # Set DISABLE_LIMITS=false before opening to other users.
    disable_limits: bool = Field(default=True, validation_alias="DISABLE_LIMITS")

    # Comma-separated Telegram numeric ids: skip daily token cap (messages + LLM billing).
    # Set to empty string to disable.
    unlimited_telegram_ids: str = Field(
        default="775766895,381905606",
        validation_alias="UNLIMITED_TELEGRAM_IDS",
    )

    @model_validator(mode="after")
    def validate_oauth_key_length(self) -> Settings:
        key = bytes.fromhex(self.oauth_encryption_key_hex)
        if len(key) != 32:
            msg = "OAUTH_ENCRYPTION_KEY must be 64 hex chars (32 bytes)"
            raise ValueError(msg)
        return self

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.app_base_url.rstrip('/')}/oauth/callback/google"

    @property
    def jira_redirect_uri(self) -> str:
        return f"{self.app_base_url.rstrip('/')}/oauth/callback/jira"

    @property
    def unlimited_telegram_id_set(self) -> frozenset[int]:
        raw = self.unlimited_telegram_ids.strip()
        if not raw:
            return frozenset()
        out: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.add(int(part))
            except ValueError:
                continue
        return frozenset(out)


@lru_cache
def get_settings() -> Settings:
    return Settings()
