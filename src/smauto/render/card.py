"""Render a tweet as a transparent PNG card using Chromium.

Drawing our own card instead of screenshotting x.com means no login session, no
cookie banners, no layout drift, and full control over fonts and theme.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape
from PIL import Image

from smauto.config import get_settings
from smauto.logging import get_logger
from smauto.render import browser as browser_mod
from smauto.render import fonts
from smauto.resolve.base import ResolvedPost

log = get_logger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
CARD_WIDTH_PX = 984
DEVICE_SCALE = 2

#: Tried in order until the card fits inside `max_height`.
FONT_LADDER = (44, 38, 34, 30, 26)

Theme = Literal["dark", "light"]

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#15202B",
        "fg": "#F7F9F9",
        "muted": "#8B98A5",
        "accent": "#1D9BF0",
        "border": "#2F3B47",
        "quote_bg": "#1B2733",
        "shadow": "rgba(0,0,0,0.45)",
        "avatar_a": "#3A5A78",
        "avatar_b": "#1D9BF0",
    },
    "light": {
        "bg": "#FFFFFF",
        "fg": "#0F1419",
        "muted": "#536471",
        "accent": "#1D9BF0",
        "border": "#E1E8ED",
        "quote_bg": "#F7F9F9",
        "shadow": "rgba(15,20,25,0.18)",
        "avatar_a": "#C9D9E6",
        "avatar_b": "#1D9BF0",
    },
}

_ENTITY = re.compile(r"(https?://\S+|[@#]\w+)", re.UNICODE)


class CardRenderError(RuntimeError):
    pass


@dataclass(slots=True)
class CardResult:
    path: Path
    width: int
    height: int
    font_size: int
    truncated: bool


def render_tweet_card(
    post: ResolvedPost,
    dest: Path,
    *,
    theme: Theme | None = None,
    width_px: int = CARD_WIDTH_PX,
    scale: int = DEVICE_SCALE,
    max_height: int | None = None,
    verified: bool = False,
) -> CardResult:
    """Render `post` to a transparent PNG at `dest`.

    Steps down through `FONT_LADDER` until the card fits `max_height` (measured
    in final frame pixels), then truncates the text as a last resort.
    """
    theme = theme or get_settings().card_theme
    dest.parent.mkdir(parents=True, exist_ok=True)

    avatar_src = _avatar_data_uri(post.author_avatar_url)
    full_text = post.text or ""

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**browser_mod.launch_kwargs())  # type: ignore[arg-type]
        try:
            page = browser.new_page(
                viewport={"width": width_px + 96, "height": 1200},
                device_scale_factor=scale,
            )

            def attempt(text: str, font_size: int) -> tuple[int, int]:
                return _measure(
                    page,
                    _build_html(
                        post,
                        theme=theme,
                        font_size=font_size,
                        width_px=width_px,
                        avatar_src=avatar_src,
                        text=text,
                        verified=verified,
                    ),
                )

            # Keep as much of the text as possible first, then as large a font as
            # possible for whatever text survived. Shrinking the font all the way
            # down before trimming produces cards nobody can read.
            limits = [len(full_text), 600, 420, 300, 200, 120]
            last: tuple[int, int, int] | None = None

            for limit in limits:
                if limit < len(full_text):
                    text = full_text[:limit].rstrip() + "…"
                elif limit > len(full_text):
                    continue
                else:
                    text = full_text

                for font_size in FONT_LADDER:
                    size = attempt(text, font_size)
                    last = (size[0], size[1], font_size)
                    if max_height is None or _frame_height(size, width_px) <= max_height:
                        _shoot(page, dest)
                        return _result(dest, font_size, truncated=limit < len(full_text))

            if last is None:  # pragma: no cover - the loop always measures once
                raise CardRenderError("could not measure the card")
            _shoot(page, dest)
            return _result(dest, last[2], truncated=True)
        finally:
            browser.close()


def _result(dest: Path, font_size: int, *, truncated: bool) -> CardResult:
    """Report the PNG's real pixel size, which is what the compositor works in."""
    with Image.open(dest) as im:
        width, height = im.width, im.height
    return CardResult(dest, width, height, font_size, truncated)


def _frame_height(size: tuple[int, int], width_px: int) -> int:
    """Convert a rendered card size to the height it will occupy in the frame."""
    w, h = size
    if w <= 0:
        return h
    return round(h * (width_px / w))


def _measure(page: Any, html: str) -> tuple[int, int]:
    page.set_content(html, wait_until="load")
    # Measuring before the webfont resolves gives fallback-metrics heights, and
    # screenshotting before it resolves gives a card in the wrong typeface.
    page.evaluate("() => document.fonts.ready")
    page.wait_for_timeout(30)
    box = page.locator("#card").bounding_box()
    if not box:
        raise CardRenderError("card element has no bounding box")
    return round(box["width"]), round(box["height"])


def _shoot(page: Any, dest: Path) -> None:
    page.locator("#card").screenshot(path=str(dest), omit_background=True, scale="device")


def _build_html(
    post: ResolvedPost,
    *,
    theme: Theme,
    font_size: int,
    width_px: int,
    avatar_src: str | None,
    text: str | None,
    verified: bool,
) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("tweet_card.html.j2")

    name = post.author_name or post.author_handle or "Anonim"
    handle = post.author_handle or "bilinmiyor"

    return template.render(
        c=THEMES[theme],
        # Generated CSS, not user input. Without Markup, autoescape turns the
        # quotes into entities and the whole @font-face block silently dies —
        # which shows up as the card rendering in a fallback serif.
        font_faces=Markup(fonts.css_font_faces()),
        font_stack=Markup(fonts.font_stack()),
        card_width=width_px,
        font_size=font_size,
        author_name=name,
        author_handle=handle,
        initial=(name.strip()[:1] or "?").upper(),
        verified=verified,
        avatar_src=avatar_src,
        text_html=_highlight(text),
        timestamp=_format_timestamp(post.created_at),
        lang=post.lang,
    )


def _highlight(text: str | None) -> Markup | None:
    """Escape the text, then colour @mentions, #hashtags and links."""
    if not text:
        return None
    parts: list[str] = []
    for chunk in _ENTITY.split(text):
        if not chunk:
            continue
        if _ENTITY.fullmatch(chunk):
            parts.append(f'<span class="ent">{escape(chunk)}</span>')
        else:
            parts.append(str(escape(chunk)))
    return Markup("".join(parts))


def _format_timestamp(dt: datetime | None) -> str:
    if dt is None:
        return ""
    months = (
        "Oca",
        "Şub",
        "Mar",
        "Nis",
        "May",
        "Haz",
        "Tem",
        "Ağu",
        "Eyl",
        "Eki",
        "Kas",
        "Ara",
    )
    return f"{dt.day} {months[dt.month - 1]} {dt.year}"


def _avatar_data_uri(url: str | None) -> str | None:
    """Inline the avatar so Chromium never makes a network request while rendering.

    Accepts either an http(s) URL (fetched once) or a local file path — your
    own profile photo is more likely to be a file on disk than a public URL.
    """
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        path = Path(url)
        if not path.is_file():
            log.debug("avatar path not found", path=url)
            return None
        try:
            mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
            return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        except OSError as exc:
            log.debug("avatar read failed", path=url, error=str(exc)[:120])
            return None
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; smauto/0.1)"})
        if resp.status_code != 200 or not resp.content:
            return None
        mime = (resp.headers.get("content-type") or "").split(";")[0].strip()
        if not mime.startswith("image/"):
            mime = mimetypes.guess_type(url)[0] or "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(resp.content).decode('ascii')}"
    except Exception as exc:
        log.debug("avatar fetch failed", url=url, error=str(exc)[:120])
        return None


#: Design width matches CARD_WIDTH_PX so it scales ~1:1 once compose() fits it
#: to the frame's content width, and needs no font-size search — its copy is
#: fixed and short, unlike a tweet's.
FOLLOW_CARD_WIDTH_PX = CARD_WIDTH_PX


def render_follow_card(
    dest: Path,
    *,
    theme: Theme | None = None,
    width_px: int = FOLLOW_CARD_WIDTH_PX,
    scale: int = DEVICE_SCALE,
) -> CardResult:
    """Render the small "follow me" pill shown under the video, to a transparent PNG."""
    theme = theme or get_settings().card_theme
    settings = get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)

    name = settings.own_account_name or settings.own_account_handle or "Hesap"
    avatar_src = _avatar_data_uri(settings.own_account_avatar)

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("follow_card.html.j2")
    html = template.render(
        c=THEMES[theme],
        font_faces=Markup(fonts.css_font_faces()),
        font_stack=Markup(fonts.font_stack()),
        card_width=width_px,
        cta_text=settings.follow_cta_text,
        author_handle=settings.own_account_handle or "hesap",
        initial=(name.strip()[:1] or "?").upper(),
        avatar_src=avatar_src,
    )

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**browser_mod.launch_kwargs())  # type: ignore[arg-type]
        try:
            page = browser.new_page(
                viewport={"width": width_px + 96, "height": 400},
                device_scale_factor=scale,
            )
            _measure(page, html)
            _shoot(page, dest)
        finally:
            browser.close()

    return _result(dest, 0, truncated=False)


__all__ = [
    "CARD_WIDTH_PX",
    "FOLLOW_CARD_WIDTH_PX",
    "FONT_LADDER",
    "THEMES",
    "CardRenderError",
    "CardResult",
    "render_follow_card",
    "render_tweet_card",
]
