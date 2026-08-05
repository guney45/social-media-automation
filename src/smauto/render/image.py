"""Compose static feed images (and text-only posts) with Pillow."""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from smauto.logging import get_logger
from smauto.render.layout import FEED, REEL, Frame, compose

log = get_logger(__name__)

BLUR_RADIUS = 30
BACKGROUND_DIM = 0.85
CORNER_RADIUS = 24
JPEG_QUALITY = 92

#: Backdrop for posts with no media at all.
GRADIENT_TOP = (26, 36, 48)
GRADIENT_BOTTOM = (12, 17, 23)


@dataclass(slots=True)
class ImageRenderResult:
    path: Path
    width: int
    height: int
    notes: list[str] = field(default_factory=list)


def render_feed(
    source: Path | None,
    card: Path | None,
    dest: Path,
    *,
    frame: Frame = FEED,
) -> ImageRenderResult:
    """Render a single still frame: blurred backdrop, media, card on top."""
    if source is None and card is None:
        raise ValueError("render_feed() needs a source image, a card, or both")

    media_img = _load_rgb(source) if source else None
    card_img = _load_rgba(card) if card else None

    placement = compose(
        frame=frame,
        card_size=card_img.size if card_img else None,
        media_size=media_img.size if media_img else None,
    )

    canvas = (
        _blurred_backdrop(media_img, frame) if media_img is not None else _gradient_backdrop(frame)
    )

    if media_img is not None and placement.media is not None:
        box = placement.media
        scaled = media_img.resize((box.width, box.height), Image.Resampling.LANCZOS)
        canvas.paste(scaled, (box.x, box.y), _rounded_mask(scaled.size, CORNER_RADIUS))

    if card_img is not None and placement.card is not None:
        box = placement.card
        scaled_card = card_img.resize((box.width, box.height), Image.Resampling.LANCZOS)
        canvas.paste(scaled_card, (box.x, box.y), scaled_card)

    dest.parent.mkdir(parents=True, exist_ok=True)
    out = canvas.convert("RGB")
    if dest.suffix.lower() in (".jpg", ".jpeg"):
        out.save(dest, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True)
    else:
        out.save(dest, "PNG", optimize=True)

    return ImageRenderResult(path=dest, width=out.width, height=out.height)


def render_story_image(source: Path | None, card: Path | None, dest: Path) -> ImageRenderResult:
    return render_feed(source, card, dest, frame=REEL)


def _load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as im:
        return im.convert("RGB")


def _load_rgba(path: Path) -> Image.Image:
    with Image.open(path) as im:
        return im.convert("RGBA")


def _blurred_backdrop(image: Image.Image, frame: Frame) -> Image.Image:
    """Cover-crop the source, blur it, and dim it so the foreground reads."""
    target_ratio = frame.width / frame.height
    src_ratio = image.width / image.height

    if src_ratio > target_ratio:
        new_h = frame.height
        new_w = max(round(image.width * (frame.height / image.height)), frame.width)
    else:
        new_w = frame.width
        new_h = max(round(image.height * (frame.width / image.width)), frame.height)

    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - frame.width) // 2
    top = (new_h - frame.height) // 2
    cropped = resized.crop((left, top, left + frame.width, top + frame.height))
    blurred = cropped.filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
    return ImageEnhance.Brightness(blurred).enhance(BACKGROUND_DIM).convert("RGBA")


def _gradient_backdrop(frame: Frame) -> Image.Image:
    canvas = Image.new("RGBA", (frame.width, frame.height))
    draw = ImageDraw.Draw(canvas)
    for y in range(frame.height):
        t = y / max(frame.height - 1, 1)
        colour = tuple(
            round(GRADIENT_TOP[i] + (GRADIENT_BOTTOM[i] - GRADIENT_TOP[i]) * t) for i in range(3)
        )
        draw.line([(0, y), (frame.width, y)], fill=(*colour, 255))
    return canvas


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (size[0] - 1, size[1] - 1)], radius=radius, fill=255)
    return mask


def accent_from(image: Image.Image) -> tuple[int, int, int]:  # pragma: no cover - cosmetic
    """Dominant, saturated colour of an image. Reserved for future card theming."""
    small = image.convert("RGB").resize((32, 32), Image.Resampling.BILINEAR)
    best: tuple[float, tuple[int, int, int]] = (0.0, (128, 128, 128))
    for pixel in list(small.getdata()):
        r, g, b = (c / 255 for c in pixel)
        _h, s, v = colorsys.rgb_to_hsv(r, g, b)
        score = s * v
        if score > best[0]:
            best = (score, pixel)
    return best[1]


__all__ = [
    "BLUR_RADIUS",
    "CORNER_RADIUS",
    "JPEG_QUALITY",
    "ImageRenderResult",
    "render_feed",
    "render_story_image",
]
