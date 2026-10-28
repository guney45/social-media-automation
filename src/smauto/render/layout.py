"""Frame geometry.

Pure functions, no I/O — this is the part that decides whether the tweet text
ends up readable or buried under Instagram's own UI, so it is worth testing on
its own.

The boxes are *anchored*, not stacked and centred as a group: the card starts
at the top of the safe area, the follow card is pinned near the bottom of the
frame at one size that never changes, and the media takes everything left
between them.
Anchoring is what makes the follow card land in the same place in every render
whatever the source was.

With no card above it the media is not confined to the safe area at all: it
fills the whole frame and the follow card is overlaid on top. That is the only
way a 9:16 source ends up edge to edge — confined to the safe area, a vertical
video has to be pillarboxed down to ~70% width to leave room for the bands it
is not allowed to enter.
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
    #: Distance from the frame's bottom edge to the follow card's bottom edge.
    #: None pins it to the bottom of the safe area.
    follow_bottom: int | None = None

    @property
    def content_width(self) -> int:
        return _even(self.width - 2 * self.side_margin)

    @property
    def content_top(self) -> int:
        return self.safe_top

    @property
    def content_height(self) -> int:
        return self.height - self.safe_top - self.safe_bottom

    @property
    def follow_floor(self) -> int:
        """Y coordinate the follow card's bottom edge sits on."""
        margin = self.safe_bottom if self.follow_bottom is None else self.follow_bottom
        return self.height - margin


#: Reels: Instagram overlays the caption, username and action buttons over the
#: bottom of the frame and a header over the top. Cards inside those bands are
#: effectively invisible, so the safe area is where the cards live. The media
#: is exempt — it is the payload, and Instagram's overlays sit on top of a
#: full-screen reel by design.
#: The follow card is the exception: it sits below the safe area, low enough
#: to give the media more room but above the username and caption lines.
REEL = Frame(
    width=1080,
    height=1920,
    safe_top=110,
    safe_bottom=360,
    side_margin=36,
    gap=16,
    follow_bottom=240,
)
STORY = Frame(width=1080, height=1920, safe_top=250, safe_bottom=250, side_margin=36, gap=16)
#: Feed posts are not overlaid, so the margins only need to look deliberate.
FEED = Frame(width=1080, height=1350, safe_top=48, safe_bottom=48, side_margin=36, gap=16)

FRAMES: dict[str, Frame] = {"reel": REEL, "story": STORY, "feed": FEED}

#: A card taller than this share of the content area starves the media.
MAX_CARD_SHARE = 0.70
#: The follow card is a nudge under the post, not a third headline. It is drawn
#: at one absolute size — not a share of the frame — so that it reads the same
#: and sits in the same place in every render.
MAX_FOLLOW_HEIGHT = 190


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
        """Cards must stay inside the safe area, where Instagram's UI is not.

        The media only has to stay inside the frame: filling it edge to edge is
        the point, not a bug to guard against.
        """
        top = self.frame.content_top
        bottom = top + self.frame.content_height
        if self.card is not None and (self.card.y < top or self.card.bottom > bottom):
            return False
        if self.follow is not None and (
            self.follow.y < top or self.follow.bottom > self.frame.follow_floor
        ):
            return False
        for box in (self.card, self.media, self.follow):
            if box is None:
                continue
            if box.x < 0 or box.x + box.width > self.frame.width:
                return False
            if box.y < 0 or box.bottom > self.frame.height:
                return False
        return True


def max_follow_height(frame: Frame) -> int:
    """The follow card is one fixed size everywhere; the frame is only consulted
    so a frame too short to hold it degrades instead of overflowing."""
    return min(MAX_FOLLOW_HEIGHT, frame.content_height // 4)


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


def covers_frame(box: Box | None, frame: Frame) -> bool:
    """True when `box` fills the frame edge to edge.

    Renderers use this to skip the blurred backdrop and the rounded corners:
    both would be entirely hidden behind the box, and rounding the corners of a
    full-frame video would punch four holes in it.
    """
    return (
        box is not None
        and box.x <= 0
        and box.y <= 0
        and box.width >= frame.width
        and box.height >= frame.height
    )


def compose(
    *,
    frame: Frame,
    card_size: tuple[int, int] | None,
    media_size: tuple[int, int] | None,
    follow_size: tuple[int, int] | None = None,
) -> Placement:
    """Anchor the card to the top of the safe area, the follow card to
    `frame.follow_floor`, and give the media everything in between.

    With no card the media is placed against the whole frame instead, so a
    vertical source fills the screen and the follow card rides on top of it.
    The card is never scaled down to make room for anything: callers pass
    `max_card_height(frame, follow_size)` to the card renderer so a long tweet
    is drawn shorter natively — at full width and still readable — and the
    media absorbs the difference, which is what "a big caption shrinks the
    video" has to mean in practice.
    """
    if card_size is None and media_size is None:
        raise ValueError("compose() needs a card, media, or both")

    top = frame.content_top
    bottom = top + frame.content_height

    follow_box = _follow_box(frame, follow_size, frame.follow_floor)
    card_box = _card_box(frame, card_size, media_size, follow_size, top)

    media_box: Box | None = None
    if media_size is not None:
        if card_box is None:
            media_box = _contain(media_size, 0, 0, frame.width, frame.height)
        else:
            band_top = card_box.bottom + frame.gap
            band_bottom = follow_box.y - frame.gap if follow_box is not None else bottom
            media_box = _contain(
                media_size, frame.side_margin, band_top, frame.content_width, band_bottom - band_top
            )

    return Placement(frame=frame, card=card_box, media=media_box, follow=follow_box)


def _follow_box(frame: Frame, follow_size: tuple[int, int] | None, bottom: int) -> Box | None:
    if follow_size is None or follow_size[0] <= 0 or follow_size[1] <= 0:
        return None
    width, height = _scale_to_width(follow_size, frame.content_width)
    cap = max_follow_height(frame)
    if height > cap:
        width, height = _scale_to_height((width, height), cap)
    return Box(x=_center_x(frame, width), y=_even(bottom - height), width=width, height=height)


def _card_box(
    frame: Frame,
    card_size: tuple[int, int] | None,
    media_size: tuple[int, int] | None,
    follow_size: tuple[int, int] | None,
    top: int,
) -> Box | None:
    if card_size is None:
        return None

    width, height = _scale_to_width(card_size, frame.content_width)
    if media_size is not None:
        # The media is the payload and the card is context, so the card never
        # takes more than its share. This is only the backstop that keeps the
        # media from collapsing to a thumbnail.
        ceiling = max_card_height(frame, follow_size)
        if height > ceiling:
            width, height = _scale_to_height((width, height), ceiling)
    else:
        limit = frame.content_height
        reserve = follow_height_in_frame(frame, follow_size)
        if reserve:
            limit -= reserve + frame.gap
        height = min(height, limit)

    return Box(x=_center_x(frame, width), y=_even(top), width=width, height=height)


def _contain(size: tuple[int, int], x: int, y: int, width: int, height: int) -> Box | None:
    """Largest box with `size`'s aspect ratio that fits the band, centred in it."""
    if width <= 0 or height <= 0:
        return None
    w, h = _scale_to_width(size, width)
    if h > height:
        w, h = _scale_to_height((w, h), height)
    if w <= 0 or h <= 0:
        return None
    return Box(x=_even(x + (width - w) // 2), y=_even(y + (height - h) // 2), width=w, height=h)


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
    "MAX_FOLLOW_HEIGHT",
    "REEL",
    "STORY",
    "Box",
    "Frame",
    "Placement",
    "Variant",
    "compose",
    "covers_frame",
    "follow_height_in_frame",
    "max_card_height",
    "max_follow_height",
]
