"""Tests that exercise the real Chromium and ffmpeg.

These are the ones that matter: Instagram rejects video for reasons that look
perfectly fine to the eye, so the acceptance criteria are ffprobe assertions,
not screenshots.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from smauto.media.probe import probe, run_ffmpeg
from smauto.render.card import FONT_LADDER, render_tweet_card
from smauto.render.image import render_feed
from smauto.render.layout import REEL, max_card_height
from smauto.render.video import MAX_DURATION_S, MIN_DURATION_S, render_reel
from smauto.resolve.base import ResolvedPost

pytestmark = pytest.mark.slow


def make_post(text: str, **kwargs) -> ResolvedPost:  # type: ignore[no-untyped-def]
    defaults = {
        "platform": "x",
        "source_id": "1",
        "source_url": "https://x.com/ornek/status/1",
        "author_handle": "ornekhesap",
        "author_name": "Örnek Hesap",
        "created_at": datetime(2026, 3, 14, tzinfo=UTC),
    }
    return ResolvedPost(text=text, **{**defaults, **kwargs})


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("media") / "clip.mp4"
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30:duration=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=8",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )
    return path


@pytest.fixture(scope="module")
def silent_short_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("media") / "short.mp4"
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=720x1280:rate=30:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    return path


@pytest.fixture(scope="module")
def long_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("media") / "long.mp4"
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1080x1080:rate=30:duration=100",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=300:duration=100",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )
    return path


@pytest.fixture(scope="module")
def photo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("media") / "photo.jpg"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1200x900", "-frames:v", "1", str(path)])
    return path


# ----------------------------------------------------------------------
# Card
# ----------------------------------------------------------------------
def test_card_renders_turkish_and_colour_emoji(tmp_path: Path, has_chromium: bool) -> None:
    if not has_chromium:
        pytest.skip("no chromium available")

    dest = tmp_path / "card.png"
    result = render_tweet_card(
        make_post("Şoförün düğmesi ığdır çğöşü 😂🇹🇷 100 kat"), dest, theme="dark"
    )

    assert dest.exists()
    with Image.open(dest) as im:
        assert im.mode == "RGBA"
        assert im.width == result.width

        # Colour emoji must survive: a monochrome fallback would leave the card
        # with only its own palette. Look for saturated non-theme pixels.
        pixels = list(im.convert("RGBA").getdata())
        saturated = [
            p for p in pixels if p[3] > 200 and (max(p[:3]) - min(p[:3])) > 60 and max(p[:3]) > 120
        ]
    assert len(saturated) > 500, "emoji appear to be rendering monochrome"


def test_card_respects_max_height(tmp_path: Path, has_chromium: bool) -> None:
    if not has_chromium:
        pytest.skip("no chromium available")

    ceiling = max_card_height(REEL)
    result = render_tweet_card(
        make_post("Çok uzun bir tweet cümlesi. " * 120), tmp_path / "long.png", max_height=ceiling
    )
    frame_height = round(result.height * (984 / result.width))
    assert frame_height <= ceiling
    assert result.truncated is True


def test_card_prefers_a_readable_font_over_the_smallest(tmp_path: Path, has_chromium: bool) -> None:
    """Trimming text then using a large font beats shrinking to 26px."""
    if not has_chromium:
        pytest.skip("no chromium available")

    result = render_tweet_card(
        make_post("Çok uzun bir tweet cümlesi. " * 120),
        tmp_path / "long.png",
        max_height=max_card_height(REEL),
    )
    assert result.font_size > FONT_LADDER[-1]


def test_short_card_is_not_truncated(tmp_path: Path, has_chromium: bool) -> None:
    if not has_chromium:
        pytest.skip("no chromium available")
    result = render_tweet_card(
        make_post("kısa"), tmp_path / "s.png", max_height=max_card_height(REEL)
    )
    assert result.truncated is False
    assert result.font_size == FONT_LADDER[0]


# ----------------------------------------------------------------------
# Video — the acceptance criteria are ffprobe facts
# ----------------------------------------------------------------------
def _assert_instagram_ready(path: Path) -> None:
    info = probe(path)
    assert (info.width, info.height) == (1080, 1920)
    assert info.pix_fmt == "yuv420p"
    assert info.video_codec == "h264"
    assert info.profile == "High"
    assert info.fps is not None and 29 <= info.fps <= 31
    assert info.has_audio, "Instagram wants an audio track even on silent clips"
    assert info.audio_codec == "aac"
    assert MIN_DURATION_S <= info.duration_ms / 1000 <= MAX_DURATION_S


def test_reel_output_is_instagram_ready(tmp_path: Path, clip: Path, has_chromium: bool) -> None:
    card = None
    if has_chromium:
        card = render_tweet_card(make_post("test 😂"), tmp_path / "card.png").path

    result = render_reel(clip, card, tmp_path / "reel.mp4")
    _assert_instagram_ready(result.path)
    assert result.width == 1080


def test_short_clip_is_looped_over_the_five_second_floor(
    tmp_path: Path, silent_short_clip: Path
) -> None:
    result = render_reel(silent_short_clip, None, tmp_path / "reel.mp4")
    _assert_instagram_ready(result.path)
    assert result.duration_ms / 1000 >= MIN_DURATION_S
    assert any("döngülendi" in note for note in result.notes)


def test_silent_source_gets_an_audio_track(tmp_path: Path, silent_short_clip: Path) -> None:
    assert probe(silent_short_clip).has_audio is False
    result = render_reel(silent_short_clip, None, tmp_path / "reel.mp4")
    assert probe(result.path).has_audio is True


def test_long_clip_is_trimmed_to_ninety_seconds(tmp_path: Path, long_clip: Path) -> None:
    result = render_reel(long_clip, None, tmp_path / "reel.mp4")
    assert result.duration_ms / 1000 <= MAX_DURATION_S
    assert any("kısaltıldı" in note for note in result.notes)


def test_faststart_moov_atom_is_at_the_front(tmp_path: Path, clip: Path) -> None:
    result = render_reel(clip, None, tmp_path / "reel.mp4")
    head = result.path.read_bytes()[:4096]
    assert b"moov" in head, "moov must be at the front (-movflags +faststart)"


def test_reel_keeps_content_out_of_the_instagram_ui_bands(
    tmp_path: Path, clip: Path, has_chromium: bool
) -> None:
    """The top and bottom safe-area bands should stay as blurred background."""
    if not has_chromium:
        pytest.skip("no chromium available")

    card = render_tweet_card(
        make_post("bant testi"), tmp_path / "card.png", max_height=max_card_height(REEL)
    ).path
    result = render_reel(clip, card, tmp_path / "reel.mp4")

    frame = tmp_path / "frame.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            "2",
            "-i",
            str(result.path),
            "-frames:v",
            "1",
            str(frame),
        ],
        check=True,
    )
    with Image.open(frame) as im:
        assert im.size == (1080, 1920)
        # A hard-edged card or video would create high local contrast; the
        # blurred backdrop does not.
        top_band = im.convert("L").crop((0, 0, 1080, REEL.safe_top))
        bottom_band = im.convert("L").crop((0, 1920 - REEL.safe_bottom, 1080, 1920))

    for band in (top_band, bottom_band):
        values = list(band.getdata())
        rowwise_jumps = sum(1 for i in range(1, len(values)) if abs(values[i] - values[i - 1]) > 60)
        assert rowwise_jumps < len(values) * 0.01


# ----------------------------------------------------------------------
# Stills
# ----------------------------------------------------------------------
def test_feed_image_is_1080x1350(tmp_path: Path, photo: Path, has_chromium: bool) -> None:
    card = (
        render_tweet_card(make_post("feed"), tmp_path / "card.png").path if has_chromium else None
    )
    result = render_feed(photo, card, tmp_path / "feed.jpg")
    assert (result.width, result.height) == (1080, 1350)
    with Image.open(result.path) as im:
        assert im.mode == "RGB"


def test_text_only_post_still_renders(tmp_path: Path, has_chromium: bool) -> None:
    if not has_chromium:
        pytest.skip("no chromium available")
    card = render_tweet_card(make_post("sadece metin"), tmp_path / "card.png").path
    result = render_feed(None, card, tmp_path / "feed.jpg")
    assert (result.width, result.height) == (1080, 1350)
