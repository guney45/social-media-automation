from __future__ import annotations

import pytest

from smauto.render.layout import (
    FEED,
    REEL,
    compose,
    follow_height_in_frame,
    max_card_height,
    max_follow_height,
)


def _within_safe_area(placement) -> bool:  # type: ignore[no-untyped-def]
    frame = placement.frame
    top = frame.content_top
    bottom = top + frame.content_height
    for box in (placement.card, placement.media, placement.follow):
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


# ----------------------------------------------------------------------
# The "follow me" card: least important box in the frame. It gets a small
# reserved slice, the card is never scaled down to make room for it (that
# shrinks the text with it), and the three boxes centre as one group.
# ----------------------------------------------------------------------
def test_follow_box_uses_leftover_space_and_the_group_stays_centred() -> None:
    """Plenty of leftover room: card and media keep their exact size, the
    follow card fits below them, and the whole three-box group re-centres
    together — no dead gap left at the bottom of the frame."""
    card, media = (1968, 600), (1280, 720)  # small media leaves lots of leftover room
    without = compose(frame=REEL, card_size=card, media_size=media)
    with_follow = compose(frame=REEL, card_size=card, media_size=media, follow_size=(1968, 300))

    assert with_follow.card is not None and without.card is not None
    assert with_follow.media is not None and without.media is not None
    assert with_follow.card.height == without.card.height
    assert with_follow.media.height == without.media.height
    assert with_follow.follow is not None
    assert with_follow.follow.y >= with_follow.media.bottom
    assert _within_safe_area(with_follow)

    top_margin = with_follow.card.y - REEL.content_top
    bottom_margin = (REEL.content_top + REEL.content_height) - with_follow.follow.bottom
    assert abs(top_margin - bottom_margin) <= 2


def test_card_is_never_scaled_narrower_than_the_content_width() -> None:
    """Scaling a finished card PNG down shrinks its text too, which is what
    made the card unreadable. A card drawn within its budget must be placed at
    full content width, follow card or not."""
    card_size = (1968, 2 * max_card_height(REEL, (1968, 244)))  # exactly the budget, at 2x
    placement = compose(
        frame=REEL, card_size=card_size, media_size=(720, 1280), follow_size=(1968, 244)
    )
    assert placement.card is not None
    assert placement.card.width == REEL.content_width


def test_card_budget_shrinks_to_reserve_room_for_the_follow_card() -> None:
    follow = (1968, 244)
    assert max_card_height(REEL, follow) < max_card_height(REEL)
    # The difference is exactly the follow card plus the gap between them.
    reserved = max_card_height(REEL) - max_card_height(REEL, follow)
    assert reserved == follow_height_in_frame(REEL, follow) + REEL.gap


def test_media_absorbs_the_reserve_and_the_group_fills_the_content_area() -> None:
    """A tall source that would otherwise fill the frame: the media gives up
    the follow card's slice, and the result still fills the content area
    exactly rather than leaving a dead band at the bottom."""
    card, media = (1968, 700), (1080, 1920)
    without = compose(frame=REEL, card_size=card, media_size=media)
    with_follow = compose(frame=REEL, card_size=card, media_size=media, follow_size=(1968, 244))

    assert with_follow.card is not None and without.card is not None
    assert with_follow.media is not None and without.media is not None
    assert with_follow.follow is not None
    # The card keeps its size and full width; the media absorbs the reserve.
    assert with_follow.card.height == without.card.height
    assert with_follow.card.width == REEL.content_width
    assert with_follow.media.height < without.media.height
    assert _within_safe_area(with_follow)

    top_margin = with_follow.card.y - REEL.content_top
    bottom_margin = (REEL.content_top + REEL.content_height) - with_follow.follow.bottom
    assert top_margin <= 2 and bottom_margin <= 2


def test_no_follow_size_means_no_follow_box() -> None:
    placement = compose(frame=REEL, card_size=(1968, 700), media_size=(1080, 1920))
    assert placement.follow is None


def test_follow_box_is_capped_even_with_abundant_leftover_space() -> None:
    placement = compose(
        frame=REEL, card_size=None, media_size=(1280, 720), follow_size=(1968, 3000)
    )
    assert placement.follow is not None
    assert placement.follow.height <= max_follow_height(REEL)


def test_follow_box_respects_aspect_ratio_when_capped() -> None:
    placement = compose(
        frame=REEL, card_size=None, media_size=(1280, 720), follow_size=(1968, 3000)
    )
    assert placement.follow is not None
    ratio = placement.follow.width / placement.follow.height
    assert abs(ratio - 1968 / 3000) < 0.02


def test_follow_box_dimensions_are_even_for_h264() -> None:
    placement = compose(
        frame=REEL, card_size=(1968, 600), media_size=(1280, 720), follow_size=(1967, 301)
    )
    assert placement.follow is not None
    assert placement.follow.width % 2 == 0
    assert placement.follow.height % 2 == 0
    assert placement.follow.x % 2 == 0
    assert placement.follow.y % 2 == 0
