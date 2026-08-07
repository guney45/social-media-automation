from __future__ import annotations

import pytest

from smauto.render.layout import FEED, REEL, compose, max_card_height


def _within_safe_area(placement) -> bool:  # type: ignore[no-untyped-def]
    frame = placement.frame
    top = frame.content_top
    bottom = top + frame.content_height
    for box in (placement.card, placement.media):
        if box is None:
            continue
        if box.y < top or box.bottom > bottom:
            return False
    return True


@pytest.mark.parametrize(
    "card,media",
    [
        ((1968, 700), (1280, 720)),
        ((1968, 700), (1080, 1920)),
        ((1968, 2600), (1280, 720)),
        ((1968, 400), (400, 400)),
        (None, (1080, 1920)),
        ((1968, 900), None),
        ((1968, 3000), None),
    ],
)
def test_boxes_stay_inside_the_safe_area(card, media) -> None:
    placement = compose(frame=REEL, card_size=card, media_size=media)
    assert placement.fits()
    assert _within_safe_area(placement)


def test_media_never_collapses_when_the_card_is_huge() -> None:
    """A very tall card must not squeeze the media down to a thumbnail."""
    placement = compose(frame=REEL, card_size=(1968, 4000), media_size=(1280, 720))
    assert placement.media is not None
    assert placement.media.height >= 300


def test_card_is_capped_when_media_is_present() -> None:
    placement = compose(frame=REEL, card_size=(1968, 4000), media_size=(1280, 720))
    assert placement.card is not None
    assert placement.card.height <= max_card_height(REEL)


def test_card_may_use_the_whole_area_when_alone() -> None:
    placement = compose(frame=REEL, card_size=(1968, 4000), media_size=None)
    assert placement.card is not None
    assert placement.card.height > max_card_height(REEL)


def test_dimensions_are_even_for_h264() -> None:
    placement = compose(frame=REEL, card_size=(1967, 701), media_size=(1281, 721))
    for box in (placement.card, placement.media):
        assert box is not None
        assert box.width % 2 == 0
        assert box.height % 2 == 0
        assert box.x % 2 == 0
        assert box.y % 2 == 0


def test_aspect_ratio_is_preserved() -> None:
    placement = compose(frame=REEL, card_size=None, media_size=(1600, 900))
    assert placement.media is not None
    ratio = placement.media.width / placement.media.height
    assert abs(ratio - 16 / 9) < 0.02


def test_content_is_vertically_centred() -> None:
    placement = compose(frame=REEL, card_size=(1968, 600), media_size=(1080, 1080))
    assert placement.card is not None and placement.media is not None
    top_gap = placement.card.y - REEL.content_top
    bottom_gap = (REEL.content_top + REEL.content_height) - placement.media.bottom
    assert abs(top_gap - bottom_gap) <= 2


def test_feed_frame_is_four_by_five() -> None:
    assert FEED.width == 1080
    assert FEED.height == 1350


def test_compose_requires_something_to_place() -> None:
    with pytest.raises(ValueError, match="needs a card"):
        compose(frame=REEL, card_size=None, media_size=None)


def test_reel_safe_area_leaves_room_for_instagram_ui() -> None:
    assert REEL.safe_top >= 100
    assert REEL.safe_bottom >= 350
    assert REEL.content_height == 1920 - REEL.safe_top - REEL.safe_bottom
