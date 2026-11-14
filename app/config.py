"""Application settings, loaded from environment / .env."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        """Trim whitespace off every string setting.

        A secret pasted into a dashboard field very often arrives with a
        trailing newline. That newline is an illegal HTTP header value, so the
        Anthropic SDK raises `APIConnectionError("Connection error.")` - which
        reads like a network fault and sends you looking in the wrong place.
        The same paste breaks Twilio signatures and Redis URLs just as quietly.
        Stripping here fixes every one of them at the point they are loaded.
        """
        return value.strip() if isinstance(value, str) else value

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

    # Retell (voice). Retell owns the telephony, speech-to-text and the voice;
    # Chichi stays the brain over the custom-LLM websocket. See docs/retell.md.
    retell_api_key: str = ""
    #: The Retell agent every shop's number points at. One agent serves all
    #: tenants: the shop is resolved per call, not per agent, so onboarding a
    #: business is a row in `channel_endpoints`, not a new Retell agent.
    retell_agent_id: str = ""
    retell_validate_signature: bool = True
    #: Shared secret sitting in the websocket path. Retell appends the call id
    #: to whatever URL is registered, so we register
    #: `wss://<host>/ws/retell/<secret>` and it connects to `.../<secret>/<id>`.
    #: Belt and braces: the call id must also match a `calls` row that only a
    #: signature-verified inbound webhook can create.
    retell_ws_secret: str = ""
    #: Blank on purpose: `python -m seeds.retell_agent --list-voices` prints the
    #: account's real options rather than this file guessing at one.
    retell_voice_id: str = ""
    #: The TTS model behind that voice, and the thing that actually decides
    #: whether a caller in Polish is spoken to in Polish. Retell's voice objects
    #: carry no language field at all - only an accent - so coverage is a
    #: property of the provider and model, not of the voice. Blank leaves
    #: Retell's own default for the chosen voice, which is cheapest and is
    #: usually English-first; `eleven_multilingual_v2` is the safe multilingual
    #: choice and costs about 4.5c/min more in TTS.
    retell_voice_model: str = ""
    #: A front-desk call that has run this long is either a mistake or abuse.
    retell_max_call_seconds: int = 600

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
    #: Run the queue worker inside the web process instead of as its own
    #: service. Halves the hosting bill at the cost of the two halves sharing a
    #: fate; see app/queue/embedded.py. Off by default.
    run_worker_in_process: bool = False

    # Agent tuning
    model_default: str = "claude-haiku-5-5"
    model_escalation: str = "claude-sonnet-5-5"
    max_tool_iterations: int = 5
    escalate_message_chars: int = 400
    language_confidence_threshold: float = 0.75

    @property
    def twilio_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token)

    @property
    def retell_configured(self) -> bool:
        """True once Retell can actually answer a call for us.

        The websocket secret counts: without it the bridge refuses every
        connection, so a key alone is not a working voice channel.
        """
        return bool(self.retell_api_key and self.retell_ws_secret)

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
