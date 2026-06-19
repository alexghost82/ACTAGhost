"""Central configuration for ACTA.

Settings are read from environment variables (prefix ``ACTA_``) and an optional
``.env`` file. Everything has a working default so ACTA boots fully offline.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ACTA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core ---
    data_dir: Path = Field(default=Path(".acta"))
    log_level: str = Field(default="INFO")
    app_name: str = Field(default="ACTA GHOST")

    # --- Security ---
    encryption_key: str | None = Field(default=None)
    master_password: str | None = Field(default=None)

    # --- AI Router / Providers ---
    default_provider: str = Field(default="mock")

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-3-5-sonnet-latest"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-1.5-flash"

    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    # --- Localization ---
    # Default UI/answer language when detection is inconclusive. One of: ru, he, en.
    default_language: str = Field(default="ru")

    # --- System control (full OS access) ---
    # When enabled, ACTA may run programs, manage processes/services and perform
    # unrestricted filesystem operations on the host. Every action is audited.
    allow_system_control: bool = Field(default=True)
    # Hard timeout (seconds) for executed shell commands.
    system_exec_timeout: int = Field(default=120)

    # --- Messaging channels ---
    telegram_bot_token: str | None = None
    # If set, ACTA uses webhook mode; otherwise it long-polls when started.
    telegram_webhook_url: str | None = None

    whatsapp_token: str | None = None
    whatsapp_phone_id: str | None = None
    whatsapp_verify_token: str = Field(default="acta-verify")

    # --- Optional backing stores ---
    postgres_dsn: str | None = None
    neo4j_uri: str | None = None
    neo4j_user: str | None = None
    neo4j_password: str | None = None
    redis_url: str | None = None

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir

    @property
    def db_path(self) -> Path:
        return self.data_dir / "acta.db"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_data_dir()
    return settings
