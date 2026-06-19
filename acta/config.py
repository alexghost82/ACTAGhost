"""Central configuration for ACTA.

Settings are read from environment variables (prefix ``ACTA_``) and an optional
``.env`` file. Everything has a working default so ACTA boots fully offline.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ACTA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core ---
    env: Literal["dev", "prod"] = Field(default="dev")
    data_dir: Path = Field(default=Path(".acta"))
    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=False)
    app_name: str = Field(default="ACTA GHOST")
    metrics_enabled: bool = Field(default=True)
    otel_enabled: bool = Field(default=False)
    sentry_dsn: str | None = Field(default=None)
    audit_max_buffer: int = Field(default=1000)
    audit_log_max_bytes: int = Field(default=5_242_880)

    # --- Security ---
    encryption_key: str | None = Field(default=None)
    master_password: str | None = Field(default=None)
    # Optional external location for Fernet key (SEC-7).
    fernet_key_path: Path | None = Field(default=None)
    # API token auth (Bearer token or X-API-Key) for protected endpoints.
    api_auth_token: str | None = Field(default=None)
    # Multi-user credentials: token:user_id:role, comma-separated.
    api_users: str = Field(default="")
    # API abuse hardening (SEC-9).
    api_max_body_size_bytes: int = Field(default=1_048_576)
    api_rate_limit_per_minute: int = Field(default=120)
    api_cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1",
            "http://localhost",
            "http://127.0.0.1:8765",
            "http://localhost:8765",
        ]
    )

    # --- AI Router / Providers ---
    default_provider: str = Field(default="mock")
    provider_max_retries: int = Field(default=2)
    provider_retry_backoff_base: float = Field(default=0.2)
    provider_breaker_threshold: int = Field(default=3)
    provider_breaker_cooldown: float = Field(default=30.0)

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-3-5-sonnet-latest"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-1.5-flash"

    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    # --- Multimodal backends (optional, offline-first) ---
    multimodal_stt_enabled: bool = Field(default=False)
    multimodal_stt_backend: str | None = Field(default=None)
    multimodal_whisper_model: str = Field(default="base")
    multimodal_openai_stt_model: str = Field(default="gpt-4o-mini-transcribe")

    multimodal_tts_enabled: bool = Field(default=False)
    multimodal_tts_backend: str | None = Field(default=None)
    multimodal_piper_binary: str = Field(default="piper")
    multimodal_piper_voice: str | None = Field(default=None)

    multimodal_vision_enabled: bool = Field(default=False)
    multimodal_vision_backend: str | None = Field(default=None)
    multimodal_vision_prompt: str = Field(
        default="Describe this image succinctly and factually for downstream reasoning."
    )

    # --- Localization ---
    # Default UI/answer language when detection is inconclusive. One of: ru, he, en.
    default_language: str = Field(default="ru")

    # --- System control (full OS access) ---
    # When enabled, ACTA may run programs, manage processes/services and perform
    # unrestricted filesystem operations on the host. Every action is audited.
    allow_system_control: bool = Field(default=False)
    # Hard timeout (seconds) for executed shell commands.
    system_exec_timeout: int = Field(default=120)
    # Optional sandbox root for destructive filesystem ops in system connector.
    system_fs_root: Path | None = Field(default=None)

    # --- Messaging channels ---
    telegram_bot_token: str | None = None
    # If set, ACTA uses webhook mode; otherwise it long-polls when started.
    telegram_webhook_url: str | None = None
    telegram_allowed_chat_ids: list[str] = Field(default_factory=list)

    whatsapp_token: str | None = None
    whatsapp_phone_id: str | None = None
    whatsapp_verify_token: str = Field(default="acta-verify")
    whatsapp_app_secret: str | None = Field(default=None)
    whatsapp_allowed_numbers: list[str] = Field(default_factory=list)
    inbound_dedupe_window_size: int = Field(default=1024)

    # --- Optional backing stores ---
    postgres_dsn: str | None = None
    neo4j_uri: str | None = None
    neo4j_user: str | None = None
    neo4j_password: str | None = None
    redis_url: str | None = None
    # Memory search backend: "auto" prefers SQLite FTS5, "legacy" keeps Python scan.
    memory_search_backend: str = Field(default="auto")
    # Journal compaction thresholds for KnowledgeGraph persistence.
    kg_compact_every_ops: int = Field(default=200)
    kg_compact_journal_bytes: int = Field(default=524_288)
    # TODO(TD-12): Postgres/pgvector backend remains roadmap P2.

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir

    @field_validator(
        "telegram_allowed_chat_ids",
        "whatsapp_allowed_numbers",
        "api_cors_origins",
        mode="before",
    )
    @classmethod
    def _parse_csv_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @property
    def db_path(self) -> Path:
        return self.data_dir / "acta.db"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.env == "prod" and not settings.api_auth_token and not settings.api_users:
        raise ValueError(
            "ACTA_ENV=prod requires API authentication (ACTA_API_AUTH_TOKEN or ACTA_API_USERS)."
        )
    settings.ensure_data_dir()
    return settings
