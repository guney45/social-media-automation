"""Configuration loading.

The blank-value cases are not hypothetical: `.env.example` ships optional keys
empty, and GitHub Actions substitutes an empty string for a secret that was
never created. Both used to crash on load with a raw pydantic error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smauto.config import Settings


def env_settings(**overrides: str) -> Settings:
    """Build settings from an explicit environment, ignoring any .env file."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Blank values behave as "unset"
# ----------------------------------------------------------------------
def test_blank_optional_int_becomes_none() -> None:
    settings = env_settings(telegram_target_chat_id="")
    assert settings.telegram_target_chat_id is None


def test_blank_string_with_a_default_falls_back_to_it() -> None:
    settings = env_settings(database_url="")
    assert settings.database_url == "sqlite:///./data/smauto.db"


def test_blank_number_falls_back_to_its_default() -> None:
    settings = env_settings(max_attempts="", ai_min_score="")
    assert settings.max_attempts == 3
    assert settings.ai_min_score == 45


def test_blank_path_becomes_none() -> None:
    assert env_settings(cookies_file="").cookies_file is None


def test_blank_slots_fall_back_to_the_default_schedule() -> None:
    settings = env_settings(publish_slots="")
    assert len(settings.slots) == 3


def test_example_env_loads_with_only_telegram_filled(tmp_path: Path) -> None:
    """The exact state a new user is in after `cp .env.example .env`."""
    example = Path(__file__).resolve().parents[1] / ".env.example"
    content = example.read_text()
    content = content.replace("TELEGRAM_BOT_TOKEN=", "TELEGRAM_BOT_TOKEN=123:fake")
    content = content.replace("TELEGRAM_ALLOWED_USER_IDS=", "TELEGRAM_ALLOWED_USER_IDS=42")

    env_file = tmp_path / ".env"
    env_file.write_text(content)

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.telegram_bot_token == "123:fake"
    assert settings.allowed_user_ids == frozenset({42})
    assert settings.telegram_target_chat_id is None


# ----------------------------------------------------------------------
# AI is optional; Instagram is not
# ----------------------------------------------------------------------
def test_ai_switches_itself_off_without_a_key() -> None:
    settings = env_settings(ai_enabled="true", anthropic_api_key="")
    assert settings.ai_enabled is False
    assert settings.ai_auto_disabled is True


def test_ai_stays_on_with_a_key() -> None:
    settings = env_settings(ai_enabled="true", anthropic_api_key="sk-ant-test")
    assert settings.ai_enabled is True
    assert settings.ai_auto_disabled is False


def test_instagram_mode_refuses_to_start_without_credentials() -> None:
    with pytest.raises(ValueError, match="IG_USER_ID"):
        env_settings(delivery_mode="instagram")


def test_instagram_mode_requires_public_storage() -> None:
    with pytest.raises(ValueError, match="STORAGE_BACKEND=r2"):
        env_settings(
            delivery_mode="instagram",
            ig_user_id="1",
            ig_access_token="t",
            storage_backend="local",
        )


def test_r2_backend_requires_its_credentials() -> None:
    with pytest.raises(ValueError, match="R2_BUCKET"):
        env_settings(storage_backend="r2")


# ----------------------------------------------------------------------
# Derived values
# ----------------------------------------------------------------------
def test_allowed_user_ids_parses_a_list() -> None:
    assert env_settings(telegram_allowed_user_ids="1, 2 ,3").allowed_user_ids == frozenset(
        {1, 2, 3}
    )


def test_resolver_chain_is_ordered() -> None:
    settings = env_settings(resolver_chain="ytdlp, fxtwitter ,manual")
    assert settings.resolver_names == ("ytdlp", "fxtwitter", "manual")


def test_slots_are_sorted() -> None:
    slots = env_settings(publish_slots="21:30,09:00,15:15").slots
    assert [s.hour for s in slots] == [9, 15, 21]


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValueError):
        env_settings(timezone="Mars/Olympus")


def test_invalid_slot_is_rejected() -> None:
    with pytest.raises(ValueError, match="HH:MM"):
        env_settings(publish_slots="yarim")


def test_score_out_of_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        env_settings(ai_min_score="500")


def test_require_telegram_needs_a_token() -> None:
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        env_settings().require_telegram()


def test_require_telegram_needs_an_allowlist() -> None:
    with pytest.raises(ValueError, match="ALLOWED_USER_IDS"):
        env_settings(telegram_bot_token="x").require_telegram()
