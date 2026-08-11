"""Application configuration, loaded and validated from the environment."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticUndefined
from pydantic_settings import BaseSettings, SettingsConfigDict

DeliveryMode = Literal["telegram", "instagram"]
StorageBackend = Literal["local", "r2"]
CardTheme = Literal["dark", "light"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---
    env: Literal["dev", "prod"] = "dev"
    database_url: str = "sqlite:///./data/smauto.db"
    data_dir: Path = Path("./data")
    log_level: str = "INFO"

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""
    telegram_target_chat_id: int | None = None

    # --- AI ---
    anthropic_api_key: str = ""
    ai_model: str = "claude-haiku-4-5-20251001"
    ai_enabled: bool = True
    ai_min_score: int = 45
    #: Set when AI was requested but no API key was supplied. Not an env var.
    ai_auto_disabled: bool = False

    # --- Render ---
    card_theme: CardTheme = "dark"
    render_reel: bool = True
    render_feed: bool = True
    render_story: bool = False

    # --- Own account branding (card shows this identity, never the source's) ---
    own_account_name: str = ""
    own_account_handle: str = ""
    #: A local file path or an https:// URL to your profile photo.
    own_account_avatar: str = ""
    own_account_verified: bool = False
    #: Shown on the small "follow" card under the video.
    follow_cta_text: str = "Daha fazlası için takip et"

    # --- Delivery ---
    delivery_mode: DeliveryMode = "telegram"
    auto_publish: bool = False

    # --- Instagram ---
    ig_user_id: str = ""
    ig_access_token: str = ""
    ig_app_id: str = ""
    ig_app_secret: str = ""

    # --- Storage ---
    storage_backend: StorageBackend = "local"
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    r2_public_base_url: str = ""

    # --- Scheduling ---
    timezone: str = "Europe/Istanbul"
    publish_slots: str = "12:30,18:30,21:30"
    max_per_day: int = 3

    # --- Resilience ---
    max_attempts: int = 3
    resolver_chain: str = "fxtwitter,ytdlp,gallerydl,manual"
    http_timeout_seconds: int = 30

    # --- Media resolution ---
    cookies_file: Path | None = None

    # --- X API (optional) ---
    x_api_enabled: bool = False
    x_bearer_token: str = ""
    x_oauth_client_id: str = ""
    x_oauth_client_secret: str = ""
    x_refresh_token: str = ""
    x_poll_source: Literal["likes", "bookmarks"] = "likes"

    # --- Secrets at rest ---
    token_encryption_key: str = ""

    # ------------------------------------------------------------------
    # Derived accessors
    # ------------------------------------------------------------------
    @property
    def allowed_user_ids(self) -> frozenset[int]:
        raw = (self.telegram_allowed_user_ids or "").strip()
        return frozenset(int(part) for part in raw.split(",") if part.strip())

    @property
    def resolver_names(self) -> tuple[str, ...]:
        return tuple(p.strip() for p in self.resolver_chain.split(",") if p.strip())

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def slots(self) -> tuple[time, ...]:
        out: list[time] = []
        for raw in self.publish_slots.split(","):
            raw = raw.strip()
            if not raw:
                continue
            hh, _, mm = raw.partition(":")
            out.append(time(hour=int(hh), minute=int(mm or 0)))
        return tuple(sorted(out))

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @field_validator("*", mode="before")
    @classmethod
    def _blank_means_unset(cls, value: Any, info: ValidationInfo) -> Any:
        """An empty environment variable means "not configured", not "empty value".

        Two places produce blanks that would otherwise crash on load: the
        shipped `.env.example` leaves optional keys empty, and GitHub Actions
        turns a secret that was never created into an empty string. Both should
        behave exactly as if the variable were absent.
        """
        if value != "" or not info.field_name:
            return value
        field = cls.model_fields.get(info.field_name)
        if field is None:
            return value
        if field.default is not PydanticUndefined:
            return field.default
        return None

    @field_validator("ai_min_score")
    @classmethod
    def _score_range(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("ai_min_score must be between 0 and 100")
        return v

    @field_validator("max_attempts", "max_per_day", "http_timeout_seconds")
    @classmethod
    def _positive(cls, v: int, info: ValidationInfo) -> int:
        if v < 1:
            raise ValueError(f"{info.field_name} must be >= 1")
        return v

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as exc:
            # ZoneInfoNotFoundError is a KeyError, which pydantic does not wrap
            # into a validation error — it would escape as a raw traceback.
            raise ValueError(f"unknown timezone {v!r} (e.g. Europe/Istanbul)") from exc
        return v

    @field_validator("publish_slots")
    @classmethod
    def _valid_slots(cls, v: str) -> str:
        for raw in v.split(","):
            raw = raw.strip()
            if not raw:
                continue
            hh, sep, mm = raw.partition(":")
            if not sep:
                raise ValueError(f"invalid slot {raw!r}, expected HH:MM")
            time(hour=int(hh), minute=int(mm))
        return v

    @model_validator(mode="after")
    def _check_mode_requirements(self) -> Settings:
        """Fail at startup, not mid-pipeline, when a mode is missing its credentials."""
        missing: list[str] = []

        if self.delivery_mode == "instagram":
            for name in ("ig_user_id", "ig_access_token"):
                if not getattr(self, name):
                    missing.append(name.upper())
            if self.storage_backend != "r2":
                missing.append("STORAGE_BACKEND=r2 (Instagram needs public media URLs)")

        if self.storage_backend == "r2":
            for name in (
                "r2_account_id",
                "r2_access_key_id",
                "r2_secret_access_key",
                "r2_bucket",
                "r2_public_base_url",
            ):
                if not getattr(self, name):
                    missing.append(name.upper())

        # AI is an enhancement, not a requirement — the pipeline has fallbacks
        # for a missing caption and a missing content verdict. Refusing to boot
        # over it would strand anyone who copied .env.example without an API
        # key, so switch it off instead and let `doctor` say so out loud.
        if self.ai_enabled and not self.anthropic_api_key:
            self.ai_enabled = False
            self.ai_auto_disabled = True

        if self.x_api_enabled and not (self.x_refresh_token or self.x_bearer_token):
            missing.append("X_REFRESH_TOKEN or X_BEARER_TOKEN")

        if missing:
            raise ValueError("Missing required configuration: " + ", ".join(missing))
        return self

    def require_telegram(self) -> None:
        """Telegram is only needed by commands that actually talk to it."""
        if not self.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set")
        if not self.allowed_user_ids:
            raise ValueError(
                "TELEGRAM_ALLOWED_USER_IDS is empty. Set it to your numeric Telegram user id "
                "so a leaked bot token cannot be used to post on your behalf."
            )


_override: Settings | None = None


def set_settings(settings: Settings | None) -> None:
    """Used by tests to inject a configuration without touching the environment."""
    global _override
    _override = settings
    get_settings.cache_clear()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    if _override is not None:
        return _override
    return Settings()


__all__ = ["Settings", "get_settings", "set_settings"]
