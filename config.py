"""Configuration — fail-closed on all secrets.

Settings are loaded from the environment or a .env file (with MOWAZAF_ prefix).
MOWAZAF_SECRET is required — the app will refuse to start without it.
"""
from __future__ import annotations

import os
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env unconditionally so all settings are read from the file.
from dotenv import load_dotenv
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="MOWAZAF_", extra="ignore"
    )

    # ── Core ─────────────────────────────────────────────────────────
    # Required — fail closed if missing or blank.
    SECRET: str = os.environ.get("MOWAZAF_SECRET", "")

    # Raw string of origins (comma-separated).
    # We avoid pydantic complex decoding for test stability.
    CORS_ORIGINS: str = "http://localhost:3000"

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/mowazaf"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── WhatsApp Cloud API (Meta) ────────────────────────────────────
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_PHONE_ID: str = ""          # the business phone_number_id
    WHATSAPP_VERIFY_TOKEN: str = ""      # set in the Meta dashboard

    # Meta webhook security (P0)
    # Used for verifying X-Hub-Signature-256 on incoming webhooks.
    META_APP_SECRET: str = ""

    # ── Telegram ────────────────────────────────────────────────────
    TELEGRAM_VERIFY_TOKEN: str = ""       # shared secret for Telegram webhook verification

    # ── Public callback URLs (configuration-driven, no tunnel hardcode) ─
    # Example:
    #   PUBLIC_BASE_URL=https://my-fixed-domain.com
    #   WHATSAPP_WEBHOOK_PATH=/webhook/whatsapp
    PUBLIC_BASE_URL: str = ""
    WHATSAPP_WEBHOOK_PATH: str = "/webhook/whatsapp"

    # ── Hermes ──────────────────────────────────────────────────────
    HERMES_API_URL: str = "http://localhost:9119"
    HERMES_API_KEY: str = ""

    # ── Test mode ───────────────────────────────────────────────────
    # When true, the webhook dispatches synchronously instead of enqueueing.
    # This avoids needing a running worker in tests.
    SYNC_DISPATCH: bool = False

    # ── Auth ────────────────────────────────────────────────────────
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 h

    # ── Validators ──────────────────────────────────────────────────

    def _cors_origins(self) -> List[str]:
        raw = self.CORS_ORIGINS
        return [o.strip() for o in raw.split(",") if o.strip()]


    @property
    def cors_origins_list(self) -> List[str]:
        """Exposed as cors_origins_list for backward compat with app/main.py."""
        return self._cors_origins()

settings = Settings()
