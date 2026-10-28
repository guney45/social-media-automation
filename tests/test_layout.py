from __future__ import annotations

import pytest

from smauto.render.layout import (
    FEED,
    MAX_FOLLOW_HEIGHT,
    REEL,
    Box,
    compose,
    covers_frame,
    follow_height_in_frame,
    max_card_height,
    max_follow_height,
)

FOLLOW = (1968, 190)


def _cards_within_safe_area(placement) -> bool:  # type: ignore[no-untyped-def]
    """The cards must stay out of Instagram's UI bands. The media may not —
    filling the frame is deliberate, see `compose()`."""
    frame = placement.frame
    top = frame.content_top
    bottom = top + frame.content_height
    card, follow = placement.card, placement.follow
    if card is not None and (card.y < top or card.bottom > bottom):
        return False
    return follow is None or (follow.y >= top and follow.bottom <= frame.follow_floor)


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
def test_boxes_stay_where_they_belong(card, media) -> None:
    placement = compose(frame=REEL, card_size=card, media_size=media, follow_size=FOLLOW)
    assert placement.fits()
    assert _cards_within_safe_area(placement)


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
# Anchoring: the card starts at the top of the safe area, the follow card is
# pinned to the bottom of it at a size that never varies, and the media fills
# what is left. This is what puts the follow card in the same place in every
# render, whatever the source was.
# ----------------------------------------------------------------------
def test_follow_card_is_pinned_to_the_follow_floor() -> None:
    placement = compose(frame=REEL, card_size=None, media_size=(720, 1280), follow_size=FOLLOW)
    assert placement.follow is not None
    assert placement.follow.bottom == REEL.follow_floor


def test_reel_follow_card_sits_below_the_safe_area_but_clear_of_the_caption() -> None:
    safe_bottom = REEL.content_top + REEL.content_height
    assert safe_bottom < REEL.follow_floor <= REEL.height - 200


def test_feed_follow_card_stays_at_the_bottom_of_the_safe_area() -> None:
    assert FEED.follow_floor == FEED.content_top + FEED.content_height


@pytest.mark.parametrize(
    "card,media",
    [
        (None, (720, 1280)),
        (None, (1280, 720)),
        ((1968, 700), (720, 1280)),
        ((1968, 4000), (1080, 1080)),
        ((1968, 300), None),
    ],
)
def test_follow_card_lands_in_the_same_place_whatever_the_source(card, media) -> None:
    """The whole point of pinning it: one fixed size, one fixed position."""
    reference = compose(frame=REEL, card_size=None, media_size=(1080, 1080), follow_size=FOLLOW)
    placement = compose(frame=REEL, card_size=card, media_size=media, follow_size=FOLLOW)

    assert reference.follow is not None and placement.follow is not None
    assert placement.follow == reference.follow


def test_card_always_starts_at_the_top_of_the_safe_area() -> None:
    for media in (None, (720, 1280), (1280, 720)):
        placement = compose(frame=REEL, card_size=(1968, 700), media_size=media, follow_size=FOLLOW)
        assert placement.card is not None
        assert placement.card.y == REEL.content_top


def test_a_taller_card_shrinks_the_media_rather_than_moving_it() -> None:
    short = compose(frame=REEL, card_size=(1968, 500), media_size=(720, 1280), follow_size=FOLLOW)
    tall = compose(frame=REEL, card_size=(1968, 1500), media_size=(720, 1280), follow_size=FOLLOW)

    assert short.media is not None and tall.media is not None
    assert tall.media.height < short.media.height
    assert short.card is not None and tall.card is not None
    assert short.card.y == tall.card.y


def test_card_is_never_scaled_narrower_than_the_content_width() -> None:
    """Scaling a finished card PNG down shrinks its text too, which is what
    made the card unreadable. A card drawn within its budget must be placed at
    full content width, follow card or not."""
    # A 2x card drawn to exactly its budget, expressed in the PNG's own pixels.
    budget = max_card_height(REEL, FOLLOW)
    card_size = (2016, int((budget - 4) * 2016 / REEL.content_width))
    placement = compose(frame=REEL, card_size=card_size, media_size=(720, 1280), follow_size=FOLLOW)
    assert placement.card is not None
    assert placement.card.width == REEL.content_width


def test_card_budget_shrinks_to_reserve_room_for_the_follow_card() -> None:
    assert max_card_height(REEL, FOLLOW) < max_card_height(REEL)
    # The difference is exactly the follow card plus the gap between them.
    reserved = max_card_height(REEL) - max_card_height(REEL, FOLLOW)
    assert reserved == follow_height_in_frame(REEL, FOLLOW) + REEL.gap


def test_no_follow_size_means_no_follow_box() -> None:
    placement = compose(frame=REEL, card_size=(1968, 700), media_size=(1080, 1920))
    assert placement.follow is None


def test_follow_box_is_capped_at_an_absolute_height() -> None:
    placement = compose(
        frame=REEL, card_size=None, media_size=(1280, 720), follow_size=(1968, 3000)
    )
    assert placement.follow is not None
    assert placement.follow.height <= max_follow_height(REEL) <= MAX_FOLLOW_HEIGHT


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


# ----------------------------------------------------------------------
# Filling the frame: without a card the media is not confined to the safe
# area, which is the only way a vertical source avoids being pillarboxed.
# ----------------------------------------------------------------------
def test_a_vertical_source_fills_the_frame_when_there_is_no_card() -> None:
    placement = compose(frame=REEL, card_size=None, media_size=(720, 1280), follow_size=FOLLOW)
    assert placement.media is not None
    assert (placement.media.width, placement.media.height) == (REEL.width, REEL.height)
    assert (placement.media.x, placement.media.y) == (0, 0)
    assert covers_frame(placement.media, REEL)


def test_the_follow_card_stays_visible_over_a_full_frame_media() -> None:
    placement = compose(frame=REEL, card_size=None, media_size=(720, 1280), follow_size=FOLLOW)
    assert placement.follow is not None
    # Overlaid on the media, but still clear of Instagram's own bottom UI.
    assert placement.follow.bottom <= REEL.follow_floor
    assert placement.fits()


def test_a_card_confines_the_media_to_the_safe_area_again() -> None:
    placement = compose(
        frame=REEL, card_size=(1968, 700), media_size=(720, 1280), follow_size=FOLLOW
    )
    assert placement.media is not None and placement.card is not None
    assert placement.media.y >= placement.card.bottom
    assert placement.follow is not None
    assert placement.media.bottom <= placement.follow.y - REEL.gap
    assert not covers_frame(placement.media, REEL)


def test_a_square_source_is_never_cropped_to_fill_the_frame() -> None:
    """Filling the frame is a contain fit, not a cover crop — a square source
    keeps every pixel and simply sits on the backdrop."""
    placement = compose(frame=REEL, card_size=None, media_size=(1080, 1080), follow_size=FOLLOW)
    assert placement.media is not None
    assert placement.media.width == REEL.width
    assert placement.media.height == REEL.width  # still square
    assert not covers_frame(placement.media, REEL)


def test_media_and_cards_sit_tight_together() -> None:
    placement = compose(
        frame=REEL, card_size=(1968, 700), media_size=(1080, 1920), follow_size=FOLLOW
    )
    assert placement.card is not None and placement.media is not None
    assert placement.follow is not None
    assert placement.media.y - placement.card.bottom <= REEL.gap + 2
    assert placement.follow.y - placement.media.bottom <= REEL.gap + 2


def test_covers_frame_is_false_for_nothing() -> None:
    assert covers_frame(None, REEL) is False
    assert covers_frame(Box(x=0, y=0, width=REEL.width, height=REEL.height - 2), REEL) is False
