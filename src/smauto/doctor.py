"""Environment and credential checks.

The single most common failure mode in a project like this is "it silently
stopped working". `doctor` exists so the answer takes ten seconds instead of an
evening.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from smauto.config import get_settings
from smauto.logging import get_logger

log = get_logger(__name__)

Level = Literal["ok", "warn", "fail"]


@dataclass(slots=True)
class Check:
    name: str
    level: Level
    detail: str


def run_checks() -> list[Check]:
    return [
        _check_ffmpeg(),
        _check_chromium(),
        _check_fonts(),
        _check_emoji_render(),
        _check_database(),
        _check_telegram(),
        _check_anthropic(),
        *_check_instagram(),
        _check_env_not_tracked(),
    ]


def _check_ffmpeg() -> Check:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        missing = ", ".join(n for n, p in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not p)
        return Check("ffmpeg", "fail", f"{missing} PATH'te yok — `apt install ffmpeg`")

    try:
        out = subprocess.run(
            [ffmpeg, "-version"], capture_output=True, text=True, timeout=15, check=False
        ).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("ffmpeg", "fail", f"çalıştırılamadı: {exc}")

    version = out.split(" version ", 1)[-1].split(" ")[0] if " version " in out else "?"
    major = version.split(".")[0]
    if major.isdigit() and int(major) < 6:
        return Check("ffmpeg", "warn", f"{version} — 6 veya üstü önerilir")
    return Check("ffmpeg", "ok", version)


def _check_chromium() -> Check:
    from smauto.render.browser import find_chromium

    found = find_chromium()
    if found:
        return Check("chromium", "ok", str(found))
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            browser.close()
        return Check("chromium", "ok", "playwright'ın kendi tarayıcısı")
    except Exception as exc:
        return Check(
            "chromium", "fail", f"bulunamadı — `playwright install chromium` ({str(exc)[:80]})"
        )


def _check_fonts() -> Check:
    from smauto.render import fonts

    status = fonts.status()
    if status.problems:
        level: Level = "fail" if status.emoji_font is None else "warn"
        return Check("fontlar", level, status.problems[0])
    return Check("fontlar", "ok", f"{status.text_font.name if status.text_font else '?'} + emoji")


def _check_emoji_render() -> Check:
    """Actually rasterise an emoji and confirm it came out in colour."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        from smauto.render import fonts

        emoji_font = fonts.find_emoji_font()
        if emoji_font is None:
            return Check("emoji testi", "fail", "renkli emoji fontu yok")

        try:
            font = ImageFont.truetype(str(emoji_font), 109)  # Noto's fixed bitmap size
        except OSError:
            return Check("emoji testi", "warn", f"font açılamadı: {emoji_font.name}")

        image = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        ImageDraw.Draw(image).text((10, 10), "😂", font=font, embedded_color=True)
        pixels = list(image.getdata())
        colours = {p[:3] for p in pixels if p[3] > 0}
        if len(colours) < 3:
            return Check("emoji testi", "warn", "emoji tek renk çıktı — fallback olabilir")
        return Check("emoji testi", "ok", f"{len(colours)} renk")
    except Exception as exc:
        return Check("emoji testi", "warn", f"çalıştırılamadı: {str(exc)[:100]}")


def _check_database() -> Check:
    try:
        from sqlalchemy import inspect

        from smauto.db.session import get_engine

        engine = get_engine()
        tables = set(inspect(engine).get_table_names())
        expected = {"items", "media_assets", "events", "blocklist", "tokens", "kv"}
        missing = expected - tables
        if missing:
            names = ", ".join(sorted(missing))
            return Check("veritabanı", "fail", f"tablolar eksik: {names} — `smauto init-db`")
        return Check("veritabanı", "ok", f"{len(tables)} tablo")
    except Exception as exc:
        return Check("veritabanı", "fail", str(exc)[:160])


def _check_telegram() -> Check:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return Check("telegram", "fail", "TELEGRAM_BOT_TOKEN boş")
    if not settings.allowed_user_ids:
        return Check(
            "telegram",
            "fail",
            "TELEGRAM_ALLOWED_USER_IDS boş — botunu herkes kullanabilir",
        )

    try:
        from smauto.telegram.client import TelegramClient

        with TelegramClient() as tg:
            me = tg.get_me()
        return Check("telegram", "ok", f"@{me.get('username')}")
    except Exception as exc:
        return Check("telegram", "fail", str(exc)[:160])


def _check_anthropic() -> Check:
    settings = get_settings()
    if not settings.ai_enabled:
        return Check("anthropic", "warn", "AI_ENABLED=false — filtre ve caption devre dışı")
    if not settings.anthropic_api_key:
        return Check("anthropic", "fail", "ANTHROPIC_API_KEY boş")

    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=settings.anthropic_api_key)
        client.messages.create(
            model=settings.ai_model,
            max_tokens=8,
            messages=[{"role": "user", "content": "ping"}],
        )
        return Check("anthropic", "ok", settings.ai_model)
    except Exception as exc:
        return Check("anthropic", "fail", f"{type(exc).__name__}: {str(exc)[:120]}")


def _check_instagram() -> list[Check]:
    settings = get_settings()
    if settings.delivery_mode != "instagram":
        return [Check("instagram", "ok", "DELIVERY_MODE=telegram — gerekmiyor")]

    checks: list[Check] = []

    try:
        from smauto.db.session import session_scope
        from smauto.publish import tokens

        with session_scope() as session:
            status = tokens.status(session)
        if not status.present:
            checks.append(Check("ig token", "fail", "token yok"))
        elif status.days_left is None:
            checks.append(
                Check("ig token", "warn", "süre bilinmiyor — `smauto refresh-tokens` çalıştır")
            )
        elif status.days_left <= 0:
            checks.append(Check("ig token", "fail", "süresi dolmuş"))
        elif status.needs_attention:
            checks.append(Check("ig token", "warn", f"{status.days_left} gün kaldı"))
        else:
            checks.append(Check("ig token", "ok", f"{status.days_left} gün kaldı"))
    except Exception as exc:
        checks.append(Check("ig token", "fail", str(exc)[:160]))

    if settings.storage_backend != "r2":
        checks.append(
            Check("depolama", "fail", "Instagram public URL istiyor — STORAGE_BACKEND=r2")
        )
    else:
        try:
            from smauto.storage.r2 import R2Storage

            R2Storage().healthcheck()
            checks.append(Check("depolama", "ok", f"r2://{settings.r2_bucket}"))
        except Exception as exc:
            checks.append(Check("depolama", "fail", str(exc)[:160]))

    return checks


def _check_env_not_tracked() -> Check:
    """A committed .env is the one mistake there is no undo for."""
    env_file = Path(".env")
    if not env_file.exists():
        return Check("secret sızıntısı", "ok", ".env yok")

    try:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".env"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return Check("secret sızıntısı", "warn", "git kontrol edilemedi")

    if proc.returncode == 0:
        return Check("secret sızıntısı", "fail", ".env git'e eklenmiş — hemen çıkar!")
    return Check("secret sızıntısı", "ok", ".env takip edilmiyor")


__all__ = ["Check", "Level", "run_checks"]
