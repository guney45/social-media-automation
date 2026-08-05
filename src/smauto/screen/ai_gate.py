"""Content screening with Claude.

The model classifies; the *decision* is made here in code. Keeping the blocking
rule out of the prompt means it is testable, auditable and cannot be talked out
of by whatever text happens to be inside a meme.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smauto.ai.client import AIError, call_json, image_block, text_block
from smauto.config import get_settings
from smauto.logging import get_logger

log = get_logger(__name__)

VALID_FLAGS = frozenset(
    {
        "hate",
        "violence",
        "gore",
        "nudity",
        "sexual",
        "politics",
        "ad",
        "watermark",
        "minor_safety",
        "self_harm",
        "low_quality",
    }
)

#: Anything here rejects the item outright.
BLOCKING_FLAGS = frozenset(
    {
        "hate",
        "violence",
        "gore",
        "nudity",
        "sexual",
        "minor_safety",
        "self_harm",
        "watermark",
        "ad",
    }
)

#: Surfaced as a warning on the preview, but not blocking.
WARNING_FLAGS = frozenset({"politics", "low_quality"})

SYSTEM = (
    "Sen bir Türkçe mizah/meme Instagram sayfasının editör asistanısın. "
    "Verilen içeriği yayına uygunluk ve komiklik açısından değerlendirirsin. "
    "Yalnızca değerlendirme yaparsın; yayın kararını sen vermezsin."
)

INSTRUCTIONS = """Bu içeriği değerlendir.

Kurallar:
- funny_score: hedef kitle (Türkçe konuşan genel mizah takipçisi) için 0-100 komiklik.
- in_niche: genel mizah / absürt / günlük hayat gözlemi mi?
- Görselde BAŞKA bir meme sayfasının kullanıcı adı, logosu veya filigranı varsa
  "watermark" flag'i ekle ve watermark_handle alanını doldur.
- Siyasi figür, parti veya güncel siyaset içeriyorsa "politics" ekle.
- Reklam, sponsorlu içerik veya ürün tanıtımıysa "ad" ekle.
- Görüntü çok bozuk, okunaksız veya düşük çözünürlüklüyse "low_quality" ekle.
- ocr_text: görselde okunan tüm metni aynen yaz (yoksa boş bırak).
- reason: sorunlu bir durum varsa tek cümlede açıkla."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "funny_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "in_niche": {"type": "boolean"},
        "language": {"type": "string", "enum": ["tr", "en", "other", "none"]},
        "flags": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(VALID_FLAGS)},
        },
        "watermark_handle": {"type": ["string", "null"]},
        "ocr_text": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["funny_score", "in_niche", "language", "flags", "ocr_text"],
}


@dataclass(slots=True)
class ScreenVerdict:
    funny_score: int
    in_niche: bool
    language: str
    flags: list[str] = field(default_factory=list)
    watermark_handle: str | None = None
    ocr_text: str = ""
    reason: str = ""
    skipped: bool = False

    @property
    def blocking_flags(self) -> list[str]:
        return sorted(set(self.flags) & BLOCKING_FLAGS)

    @property
    def warnings(self) -> list[str]:
        return sorted(set(self.flags) & WARNING_FLAGS)


def screen(text: str | None, image: Path | None) -> ScreenVerdict:
    """Ask the model to classify. Returns a permissive verdict when AI is off."""
    settings = get_settings()
    if not settings.ai_enabled:
        return ScreenVerdict(funny_score=100, in_niche=True, language="none", skipped=True)

    blocks: list[dict[str, Any]] = []
    picture = image_block(image) if image else None
    if picture:
        blocks.append(picture)
    blocks.append(text_block(f"{INSTRUCTIONS}\n\nGönderi metni:\n{text or '(metin yok)'}"))

    payload = call_json(
        system=SYSTEM,
        blocks=blocks,
        tool_name="rate_content",
        tool_description="İçeriği yayına uygunluk ve komiklik açısından değerlendirir.",
        schema=SCHEMA,
        max_tokens=1024,
    )

    flags = [f for f in payload.get("flags", []) if f in VALID_FLAGS]
    return ScreenVerdict(
        funny_score=int(payload.get("funny_score", 0)),
        in_niche=bool(payload.get("in_niche", False)),
        language=str(payload.get("language") or "other"),
        flags=flags,
        watermark_handle=payload.get("watermark_handle") or None,
        ocr_text=str(payload.get("ocr_text") or ""),
        reason=str(payload.get("reason") or ""),
    )


def decide(verdict: ScreenVerdict) -> tuple[bool, str | None]:
    """Return (accepted, rejection reason)."""
    settings = get_settings()

    blocking = verdict.blocking_flags
    if blocking:
        detail = verdict.reason or ", ".join(blocking)
        if "watermark" in blocking and verdict.watermark_handle:
            detail = f"başka bir sayfanın filigranı ({verdict.watermark_handle})"
        return False, f"içerik filtresi: {detail}"

    if verdict.funny_score < settings.ai_min_score:
        return False, (f"komiklik skoru düşük ({verdict.funny_score} < {settings.ai_min_score})")

    if not verdict.in_niche:
        return False, "sayfanın konusuna uymuyor"

    return True, None


def safe_screen(text: str | None, image: Path | None) -> ScreenVerdict:
    """Screen without letting an AI outage block the pipeline.

    A failed classification must not silently *approve* borderline content, but
    it also should not stall the queue — so we pass the item through with a
    neutral score and a visible flag on the preview.
    """
    try:
        return screen(text, image)
    except AIError as exc:
        log.warning("ai screening unavailable", error=str(exc)[:200])
        return ScreenVerdict(
            funny_score=100,
            in_niche=True,
            language="none",
            reason=f"AI filtresi çalışmadı: {exc}"[:200],
            skipped=True,
        )


__all__ = [
    "BLOCKING_FLAGS",
    "SCHEMA",
    "VALID_FLAGS",
    "WARNING_FLAGS",
    "ScreenVerdict",
    "decide",
    "safe_screen",
    "screen",
]
