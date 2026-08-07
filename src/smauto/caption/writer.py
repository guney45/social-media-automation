"""Generate the Instagram caption and hashtags."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from smauto.ai.client import AIError, call_json, text_block
from smauto.config import get_settings
from smauto.logging import get_logger

log = get_logger(__name__)

SYSTEM = (
    "Türkçe bir mizah Instagram sayfası için caption yazarsın. "
    "Kısa, doğal ve samimi yazarsın; kalıplaşmış sosyal medya dilinden kaçınırsın."
)

INSTRUCTIONS = """Bu içerik için Instagram caption'ı yaz.

Kurallar:
- 1-2 satır. Tweet metnini aynen tekrarlama; üzerine bir yorum veya tepki yaz.
- Emoji kullanabilirsin ama abartma (en fazla 2-3 tane).
- "Beğen ve kaydet", "yorumlarda buluşalım", "etiketle" gibi kalıplar YASAK.
- Clickbait yok, soru sorup cevap bekleme.
- hashtags: 8-15 adet, '#' dahil, karışık Türkçe/İngilizce.
  İçerikle gerçekten ilgili olsunlar; jenerik #keşfet #kesfetteyiz spam'i yapma.
- Kaynak etiketini SEN yazma; sistem otomatik ekliyor."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["caption", "hashtags"],
}

MAX_HASHTAGS = 15
FALLBACK_HASHTAGS = ["#mizah", "#komik", "#meme", "#kahkaha", "#gündem", "#funny"]


@dataclass(slots=True)
class Caption:
    caption: str
    hashtags: list[str] = field(default_factory=list)
    generated: bool = True


def write(text: str | None, ocr_text: str = "", *, author_handle: str | None = None) -> Caption:
    """Ask the model for a caption; fall back to something usable when AI is off."""
    settings = get_settings()
    if not settings.ai_enabled:
        return _fallback(text)

    prompt = INSTRUCTIONS + f"\n\nGönderi metni:\n{text or '(metin yok)'}"
    if ocr_text.strip():
        prompt += f"\n\nGörselde okunan metin:\n{ocr_text.strip()}"
    if author_handle:
        prompt += f"\n\nKaynak yazar: @{author_handle}"

    payload = call_json(
        system=SYSTEM,
        blocks=[text_block(prompt)],
        tool_name="write_caption",
        tool_description="Instagram caption'ı ve hashtag listesi üretir.",
        schema=SCHEMA,
        max_tokens=800,
    )

    return Caption(
        caption=str(payload.get("caption") or "").strip(),
        hashtags=_normalise(payload.get("hashtags") or []),
    )


def safe_write(
    text: str | None, ocr_text: str = "", *, author_handle: str | None = None
) -> Caption:
    """Never let a caption outage block delivery — the user can always edit it."""
    try:
        return write(text, ocr_text, author_handle=author_handle)
    except AIError as exc:
        log.warning("caption generation unavailable", error=str(exc)[:200])
        return _fallback(text)


def _fallback(text: str | None) -> Caption:
    body = (text or "").strip()
    if len(body) > 180:
        body = body[:180].rstrip() + "…"
    return Caption(caption=body, hashtags=list(FALLBACK_HASHTAGS), generated=False)


def _normalise(raw: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for entry in raw:
        tag = str(entry).strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag
        tag = tag.replace(" ", "")
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= MAX_HASHTAGS:
            break
    return out


__all__ = ["FALLBACK_HASHTAGS", "MAX_HASHTAGS", "Caption", "safe_write", "write"]
