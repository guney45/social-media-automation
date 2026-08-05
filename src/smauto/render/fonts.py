"""Font discovery and installation.

Turkish glyphs and colour emoji are the two things that silently break tweet
cards. Both are checked here and by `smauto doctor`.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx

from smauto.logging import get_logger

log = get_logger(__name__)

FONT_DIR = Path(__file__).parent / "fonts"

TEXT_FONT_FILE = FONT_DIR / "InterVariable.ttf"
EMOJI_FONT_FILE = FONT_DIR / "NotoColorEmoji.ttf"

TEXT_FONT_URL = (
    "https://raw.githubusercontent.com/rsms/inter/master/docs/font-files/InterVariable.ttf"
)
EMOJI_FONT_URL = (
    "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/fonts/NotoColorEmoji.ttf"
)

#: Fallbacks that ship with most Linux images, used when the bundled files are absent.
SYSTEM_EMOJI_CANDIDATES = (
    Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
    Path("/usr/share/fonts/noto/NotoColorEmoji.ttf"),
    Path("/usr/share/fonts/truetype/noto-color-emoji/NotoColorEmoji.ttf"),
    Path("/System/Library/Fonts/Apple Color Emoji.ttc"),
)


@dataclass(frozen=True, slots=True)
class FontStatus:
    text_font: Path | None
    emoji_font: Path | None

    @property
    def ok(self) -> bool:
        return self.emoji_font is not None

    @property
    def problems(self) -> list[str]:
        issues: list[str] = []
        if self.emoji_font is None:
            issues.append(
                "No colour emoji font found. Emoji will render as empty boxes. "
                "Run `smauto fetch-fonts` or install the fonts-noto-color-emoji package."
            )
        if self.text_font is None:
            issues.append(
                "Inter not bundled; falling back to a system sans-serif. "
                "Run `smauto fetch-fonts` for the intended look."
            )
        return issues


def status() -> FontStatus:
    return FontStatus(text_font=find_text_font(), emoji_font=find_emoji_font())


def find_text_font() -> Path | None:
    return TEXT_FONT_FILE if TEXT_FONT_FILE.exists() else None


def find_emoji_font() -> Path | None:
    if EMOJI_FONT_FILE.exists():
        return EMOJI_FONT_FILE
    for candidate in SYSTEM_EMOJI_CANDIDATES:
        if candidate.exists():
            return candidate
    found = _fc_match("Noto Color Emoji")
    return found


def fetch_fonts(*, force: bool = False, timeout: int = 120) -> list[Path]:
    """Download the bundled fonts. Called by `smauto fetch-fonts` and the Dockerfile."""
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []

    for url, dest in ((TEXT_FONT_URL, TEXT_FONT_FILE), (EMOJI_FONT_URL, EMOJI_FONT_FILE)):
        if dest.exists() and not force:
            log.info("font already present", file=dest.name)
            continue
        log.info("downloading font", file=dest.name)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        downloaded.append(dest)

    return downloaded


#: Embedding a 10 MB emoji font in every page is wasteful, so it is only done
#: when fontconfig has nothing for Chromium to fall back on.
_MAX_INLINE_BYTES = 4 * 1024 * 1024


@lru_cache(maxsize=4)
def _data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def css_font_faces() -> str:
    """`@font-face` rules for the card template.

    Fonts must be inlined as data URIs: pages rendered via `set_content` have an
    opaque origin, and fonts are CORS-restricted, so a `file://` src is silently
    dropped and the card falls back to a system serif.
    """
    blocks: list[str] = []

    text_font = find_text_font()
    if text_font:
        blocks.append(
            "@font-face{font-family:'InterBundled';"
            f"src:url({_data_uri(text_font, 'font/ttf')}) format('truetype');"
            "font-weight:100 900;font-style:normal;font-display:block;}"
        )

    emoji_font = find_emoji_font()
    if emoji_font and not _system_emoji_available():
        if emoji_font.stat().st_size <= _MAX_INLINE_BYTES:
            blocks.append(
                "@font-face{font-family:'EmojiBundled';"
                f"src:url({_data_uri(emoji_font, 'font/ttf')});font-display:block;}}"
            )
        else:
            log.warning(
                "emoji font is too large to inline and fontconfig has no fallback; "
                "emoji may render as boxes",
                file=str(emoji_font),
            )

    return "\n".join(blocks)


def font_stack() -> str:
    """Ordered font-family list for the card.

    Order matters twice over. The colour emoji font must come *before* the
    generic fallbacks, or DejaVu/Unifont answer first and emoji render as
    monochrome outlines. It must come *after* the Latin font, because Noto
    Color Emoji also maps ASCII digits and `#` (for keycap sequences) and would
    otherwise hijack them.
    """
    families: list[str] = []
    if find_text_font():
        families.append("'InterBundled'")
    families.append("'Inter'")

    if find_emoji_font() and not _system_emoji_available():
        families.append("'EmojiBundled'")
    families.extend(["'Noto Color Emoji'", "'Apple Color Emoji'", "'Segoe UI Emoji'"])

    families.extend(["'Noto Sans'", "'DejaVu Sans'", "system-ui", "sans-serif"])
    return ", ".join(families)


@lru_cache(maxsize=1)
def _system_emoji_available() -> bool:
    """True when Chromium can reach a colour emoji font through fontconfig."""
    if any(p.exists() for p in SYSTEM_EMOJI_CANDIDATES):
        return True
    return _fc_match("Noto Color Emoji") is not None


def _fc_match(family: str) -> Path | None:
    binary = shutil.which("fc-match")
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "-f", "%{file}", family],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - platform dependent
        return None
    if proc.returncode != 0:
        return None
    candidate = Path(proc.stdout.strip())
    if candidate.exists() and "emoji" in candidate.name.lower():
        return candidate
    return None


__all__ = [
    "EMOJI_FONT_FILE",
    "FONT_DIR",
    "TEXT_FONT_FILE",
    "FontStatus",
    "css_font_faces",
    "fetch_fonts",
    "find_emoji_font",
    "find_text_font",
    "font_stack",
    "status",
]
