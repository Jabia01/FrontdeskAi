"""Application settings, loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Claude
    anthropic_api_key: str = ""

    # Infra
    database_url: str = (
        "postgresql+asyncpg://frontdesk:frontdesk@localhost:5434/frontdesk"
    )
    redis_url: str = "redis://localhost:6380"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_validate_signature: bool = True
    public_base_url: str = "http://localhost:8000"

    # Google Calendar (service account; blank = in-memory stub).
    #: A path on disk - fine locally.
    google_credentials_path: str = ""
    #: The whole service-account JSON as one value. What a managed platform
    #: with no persistent disk can actually give us. Takes precedence over the
    #: path when both are set.
    google_credentials_json: str = ""

    # Behaviour
    ai_enabled: bool = True
    log_level: str = "INFO"

    # Agent tuning
    model_default: str = "claude-haiku-5-5"
    model_escalation: str = "claude-sonnet-5-5"
    max_tool_iterations: int = 5
    escalate_message_chars: int = 400
    language_confidence_threshold: float = 0.75

    @property
    def twilio_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token)

    #: Set this when DATABASE_URL points at a *transaction mode* pooler
    #: (Supabase's port 6543, or PgBouncer). Such a pooler hands a different
    #: backend to each transaction, so a prepared statement created on one is
    #: missing on the next and asyncpg fails with "prepared statement does not
    #: exist" under load - intermittently, which is the worst kind. Session
    #: mode and direct connections do not need this; see docs/deploy.md.
    db_disable_prepared_statements: bool = False

    @property
    def calendar_configured(self) -> bool:
        return bool(self.google_credentials_json or self.google_credentials_path)


@lru_cache
def get_settings() -> Settings:
    return Settings()
