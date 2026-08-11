"""Frame geometry.

Pure functions, no I/O — this is the part that decides whether the tweet text
ends up readable or buried under Instagram's own UI, so it is worth testing on
its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Variant = Literal["reel", "feed", "story"]


@dataclass(frozen=True, slots=True)
class Frame:
    width: int
    height: int
    safe_top: int
    safe_bottom: int
    side_margin: int
    gap: int

    @property
    def content_width(self) -> int:
        return _even(self.width - 2 * self.side_margin)

    @property
    def content_top(self) -> int:
        return self.safe_top

    @property
    def content_height(self) -> int:
        return self.height - self.safe_top - self.safe_bottom


#: Reels: Instagram overlays the caption, username and action buttons over the
#: bottom of the frame and a header over the top. Anything inside those bands is
#: effectively invisible.
REEL = Frame(width=1080, height=1920, safe_top=140, safe_bottom=400, side_margin=48, gap=32)
STORY = Frame(width=1080, height=1920, safe_top=250, safe_bottom=250, side_margin=48, gap=32)
#: Feed posts are not overlaid, so the margins only need to look deliberate.
FEED = Frame(width=1080, height=1350, safe_top=60, safe_bottom=60, side_margin=48, gap=32)

FRAMES: dict[str, Frame] = {"reel": REEL, "story": STORY, "feed": FEED}

#: A card taller than this share of the content area starves the media.
MAX_CARD_SHARE = 0.70
#: The follow card is the least important thing in the frame — a nudge under
#: the post, not a third headline. It never claims more than this share.
MAX_FOLLOW_SHARE = 0.10


@dataclass(frozen=True, slots=True)
class Box:
    x: int
    y: int
    width: int
    height: int

    @property
    def bottom(self) -> int:
        return self.y + self.height


@dataclass(frozen=True, slots=True)
class Placement:
    frame: Frame
    card: Box | None
    media: Box | None
    follow: Box | None = None

    def fits(self) -> bool:
        top = self.frame.content_top
        bottom = top + self.frame.content_height
        for box in (self.card, self.media, self.follow):
            if box is None:
                continue
            if box.y < top or box.bottom > bottom:
                return False
            if box.x < 0 or box.x + box.width > self.frame.width:
                return False
        return True


def max_follow_height(frame: Frame) -> int:
    return int(frame.content_height * MAX_FOLLOW_SHARE)


def follow_height_in_frame(frame: Frame, follow_size: tuple[int, int] | None) -> int:
    """How much vertical room a follow card of `follow_size` will occupy, 0 for none."""
    if follow_size is None or follow_size[0] <= 0 or follow_size[1] <= 0:
        return 0
    _w, height = _scale_to_width(follow_size, frame.content_width)
    return min(height, max_follow_height(frame))


def max_card_height(frame: Frame, follow_size: tuple[int, int] | None = None) -> int:
    """Tallest the card may be drawn.

    Pass `follow_size` when a follow card is going into the same frame: the
    card renderer then draws a natively shorter card (smaller font, or less
    text) at full width, instead of `compose()` having to scale the finished
    PNG down — which shrinks the text with it and makes the card unreadable.
    """
    reserve = follow_height_in_frame(frame, follow_size)
    if reserve:
        reserve += frame.gap
    return int(frame.content_height * MAX_CARD_SHARE) - reserve


def compose(
    *,
    frame: Frame,
    card_size: tuple[int, int] | None,
    media_size: tuple[int, int] | None,
    follow_size: tuple[int, int] | None = None,
) -> Placement:
    """Stack an optional card, media and follow card, centred as one group in
    the safe area.

    The card and the media are the two things worth looking at, so they are
    sized first and the media absorbs whatever does not fit — the text is the
    joke, so it keeps its size.

    `follow_size`, when given, reserves room for a small "follow me" card
    under the media *before* the media is sized, so the three boxes fill the
    content area as one centred group with no dead gap at the bottom. The
    card is never scaled down to make that room: callers pass
    `max_card_height(frame, follow_size)` to the card renderer so the card is
    drawn shorter natively, at full width and still readable.
    """
    if card_size is None and media_size is None:
        raise ValueError("compose() needs a card, media, or both")

    content_w = frame.content_width
    avail_h = frame.content_height

    # Reserve the follow card up front — it is the least important box, so it
    # gets a fixed small slice and never competes for the rest.
    follow_w = follow_h = 0
    if follow_size is not None and follow_size[0] > 0 and follow_size[1] > 0:
        follow_w, follow_h = _scale_to_width(follow_size, content_w)
        cap = max_follow_height(frame)
        if follow_h > cap:
            follow_w, follow_h = _scale_to_height((follow_w, follow_h), cap)

    follow_gap = frame.gap if follow_h > 0 else 0
    budget_h = avail_h - follow_h - follow_gap

    card_w = card_h = 0
    if card_size is not None:
        card_w, card_h = _scale_to_width(card_size, content_w)
        if media_size is not None:
            # With media present the media is the payload and the card is context,
            # so the card never takes more than its share. Callers avoid this by
            # passing `max_card_height()` to the card renderer; this is only the
            # backstop that keeps the media from collapsing to a thumbnail.
            ceiling = int(frame.content_height * MAX_CARD_SHARE)
            if card_h > ceiling:
                card_w, card_h = _scale_to_height((card_w, card_h), ceiling)
        else:
            card_h = min(card_h, budget_h)

    media_w = media_h = 0
    if media_size is not None:
        media_w, media_h = _scale_to_width(media_size, content_w)

    gap = frame.gap if (card_size is not None and media_size is not None) else 0
    total = card_h + gap + media_h

    if total > budget_h and media_size is not None:
        media_budget = max(budget_h - card_h - gap, 0)
        media_w, media_h = _scale_to_height((media_w, media_h), media_budget)
        total = card_h + gap + media_h

    group = total + (follow_gap + follow_h if follow_h > 0 else 0)
    top = frame.content_top + max((avail_h - group) // 2, 0)

    card_box = None
    media_box = None
    follow_box = None
    cursor = top

    if card_size is not None and card_h > 0:
        card_box = Box(x=_center_x(frame, card_w), y=_even(cursor), width=card_w, height=card_h)
        cursor = card_box.bottom + gap

    if media_size is not None and media_h > 0:
        media_box = Box(x=_center_x(frame, media_w), y=_even(cursor), width=media_w, height=media_h)
        cursor = media_box.bottom

    if follow_h > 0:
        cursor += follow_gap
        follow_box = Box(
            x=_center_x(frame, follow_w), y=_even(cursor), width=follow_w, height=follow_h
        )

    return Placement(frame=frame, card=card_box, media=media_box, follow=follow_box)


def _scale_to_width(size: tuple[int, int], target_w: int) -> tuple[int, int]:
    w, h = size
    if w <= 0 or h <= 0:
        return 0, 0
    scale = target_w / w
    return _even(target_w), _even(round(h * scale))


def _scale_to_height(size: tuple[int, int], target_h: int) -> tuple[int, int]:
    w, h = size
    if w <= 0 or h <= 0:
        return 0, 0
    scale = target_h / h
    return _even(round(w * scale)), _even(target_h)


def _center_x(frame: Frame, width: int) -> int:
    return _even((frame.width - width) // 2)


def _even(value: int) -> int:
    """H.264 with yuv420p requires even dimensions and behaves better on even offsets."""
    v = int(value)
    return v - (v % 2)


__all__ = [
    "FEED",
    "FRAMES",
    "MAX_CARD_SHARE",
    "MAX_FOLLOW_SHARE",
    "REEL",
    "STORY",
    "Box",
    "Frame",
    "Placement",
    "Variant",
    "compose",
    "follow_height_in_frame",
    "max_card_height",
    "max_follow_height",
]
