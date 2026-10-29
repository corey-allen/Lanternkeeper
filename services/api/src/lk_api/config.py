"""Configuration from environment variables (prefix ``LK_``). Secrets never have defaults."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LK_", env_file=None, extra="ignore")

    environment: Literal["production", "development", "test"] = "production"
    database_url: str = Field(description="postgresql+psycopg://user:pass@host/db")
    storage_dir: str = "/data/storage"
    secret_key: SecretStr = Field(description="HMAC key for session tokens; at least 32 chars")
    admin_username: str = "keeper"
    admin_password: SecretStr | None = None
    session_hours: int = 12
    cookie_secure: bool = True

    max_upload_mb: int = 50
    max_pages: int = 400
    ocr_engine: Literal["none", "tesseract"] = "none"
    ocr_min_chars: int = 40  # pages with fewer extracted characters are OCR candidates

    ai_provider: Literal["fake", "openai_compatible"] = "fake"
    ai_base_url: str | None = None
    ai_api_key: SecretStr | None = None
    ai_model: str = "local-model"
    ai_allow_remote: bool = False
    ai_local_hosts: str = "localhost,127.0.0.1,::1,host.docker.internal,ollama"
    ai_timeout_seconds: float = 30.0
    ai_max_attempts: int = 2

    dice_mode: Literal["secure", "seeded"] = "secure"
    dice_seed: int = 0

    log_level: str = "INFO"
    log_document_text: bool = False

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if len(self.secret_key.get_secret_value()) < 32:
            raise ValueError("LK_SECRET_KEY must be at least 32 characters")
        if self.dice_mode == "seeded" and self.environment != "test":
            raise ValueError("LK_DICE_MODE=seeded is only allowed when LK_ENVIRONMENT=test")
        return self

    @property
    def local_hosts(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.ai_local_hosts.split(",") if h.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
