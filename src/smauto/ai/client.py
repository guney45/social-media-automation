"""Shared Anthropic client helpers.

Both AI calls need a guaranteed-shape JSON answer, so they go through a forced
tool call rather than asking for JSON in prose and hoping.
"""

from __future__ import annotations

import base64
import mimetypes
from functools import lru_cache
from pathlib import Path
from typing import Any

from smauto.config import get_settings
from smauto.logging import get_logger

log = get_logger(__name__)

#: Anthropic accepts these for image blocks.
SUPPORTED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 4 * 1024 * 1024


class AIError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _client() -> Any:
    from anthropic import Anthropic

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise AIError("ANTHROPIC_API_KEY is not set (or set AI_ENABLED=false)")
    return Anthropic(api_key=settings.anthropic_api_key)


def call_json(
    *,
    system: str,
    blocks: list[dict[str, Any]],
    tool_name: str,
    tool_description: str,
    schema: dict[str, Any],
    max_tokens: int = 1024,
) -> dict[str, Any]:
    """Run one message turn and return the forced tool call's arguments."""
    settings = get_settings()
    client = _client()

    try:
        response = client.messages.create(
            model=settings.ai_model,
            max_tokens=max_tokens,
            system=system,
            tools=[
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": blocks}],
        )
    except Exception as exc:
        raise AIError(f"{type(exc).__name__}: {exc}"[:400]) from exc

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == tool_name:
            payload = getattr(block, "input", None)
            if isinstance(payload, dict):
                return payload

    raise AIError("model did not return the expected tool call")


def image_block(path: Path) -> dict[str, Any] | None:
    """Base64 image block, or None when the file cannot be used as one."""
    if not path.exists():
        return None

    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if mime not in SUPPORTED_IMAGE_MIMES:
        return None
    if path.stat().st_size > MAX_IMAGE_BYTES:
        downscaled = _downscale(path)
        if downscaled is None:
            return None
        path, mime = downscaled, "image/jpeg"

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": mime,
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        },
    }


def _downscale(path: Path) -> Path | None:
    """Shrink an oversized image so it fits the API's per-image budget."""
    try:
        from PIL import Image

        dest = path.with_suffix(".ai.jpg")
        with Image.open(path) as opened:
            converted = opened.convert("RGB")
            converted.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
            converted.save(dest, "JPEG", quality=82, optimize=True)
        return dest if dest.stat().st_size <= MAX_IMAGE_BYTES else None
    except Exception as exc:
        log.debug("could not downscale image for AI", path=str(path), error=str(exc)[:120])
        return None


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


__all__ = ["AIError", "call_json", "image_block", "text_block"]
