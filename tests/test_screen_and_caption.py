from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from sqlalchemy.orm import Session

from smauto.caption import writer
from smauto.config import Settings, set_settings
from smauto.db import states
from smauto.db.models import Item
from smauto.screen import dedupe
from smauto.screen.ai_gate import ScreenVerdict, decide


# ----------------------------------------------------------------------
# Dedupe
# ----------------------------------------------------------------------
def _make_image(path: Path, seed: int, size=(800, 600)) -> Path:
    image = Image.new("RGB", size, (20 + seed % 50, 90, 160))
    draw = ImageDraw.Draw(image)
    for i in range(12):
        offset = (i * 37 + seed * 53) % size[0]
        draw.ellipse(
            [offset, (i * 29 + seed) % size[1], offset + 120, ((i * 29 + seed) % size[1]) + 110],
            fill=((seed * 37 + i * 20) % 255, (i * 40) % 255, (seed * 11) % 255),
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    return path


def test_phash_survives_resize_and_jpeg(tmp_path: Path) -> None:
    original = _make_image(tmp_path / "a.png", seed=7)

    with Image.open(original) as im:
        smaller = im.resize((int(im.width * 0.7), int(im.height * 0.7)), Image.Resampling.LANCZOS)
        recompressed = tmp_path / "a_small.jpg"
        smaller.convert("RGB").save(recompressed, "JPEG", quality=60)

    a = dedupe.compute_phash(original)
    b = dedupe.compute_phash(recompressed)
    assert a and b
    assert dedupe.hamming(a, b) <= dedupe.THRESHOLD


def test_phash_separates_unrelated_images(tmp_path: Path) -> None:
    a = dedupe.compute_phash(_make_image(tmp_path / "a.png", seed=3))
    b = dedupe.compute_phash(_make_image(tmp_path / "b.png", seed=91))
    assert a and b
    assert dedupe.hamming(a, b) > dedupe.THRESHOLD


def test_find_duplicate_matches_a_stored_item(session: Session, tmp_path: Path) -> None:
    phash = dedupe.compute_phash(_make_image(tmp_path / "a.png", seed=5))
    assert phash

    existing = Item(
        source_platform="x", source_url="u", source_id="1", status=states.PUBLISHED, phash=phash
    )
    session.add(existing)
    session.flush()

    found = dedupe.find_duplicate(session, phash, exclude_id=999)
    assert found is not None
    assert found[0] == existing.id
    assert found[1] == 0


def test_find_duplicate_excludes_self(session: Session, tmp_path: Path) -> None:
    phash = dedupe.compute_phash(_make_image(tmp_path / "a.png", seed=5))
    assert phash
    item = Item(
        source_platform="x", source_url="u", source_id="1", status=states.SCREENED, phash=phash
    )
    session.add(item)
    session.flush()
    assert dedupe.find_duplicate(session, phash, exclude_id=item.id) is None


def test_hamming_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="lengths differ"):
        dedupe.hamming("abcd", "abcdef")


# ----------------------------------------------------------------------
# AI gate decision logic (the model classifies; this code decides)
# ----------------------------------------------------------------------
def _verdict(**kwargs) -> ScreenVerdict:  # type: ignore[no-untyped-def]
    base = {"funny_score": 80, "in_niche": True, "language": "tr", "flags": []}
    return ScreenVerdict(**{**base, **kwargs})


@pytest.mark.parametrize("flag", ["hate", "gore", "nudity", "sexual", "minor_safety", "ad"])
def test_blocking_flags_reject(flag: str) -> None:
    accepted, reason = decide(_verdict(flags=[flag]))
    assert accepted is False
    assert reason


def test_watermark_names_the_offending_account() -> None:
    accepted, reason = decide(_verdict(flags=["watermark"], watermark_handle="@baskasayfa"))
    assert accepted is False
    assert reason is not None and "@baskasayfa" in reason


def test_politics_is_a_warning_not_a_block() -> None:
    verdict = _verdict(flags=["politics"])
    accepted, _ = decide(verdict)
    assert accepted is True
    assert verdict.warnings == ["politics"]


def test_low_score_rejects() -> None:
    accepted, reason = decide(_verdict(funny_score=10))
    assert accepted is False
    assert reason is not None and "skor" in reason


def test_off_niche_rejects() -> None:
    accepted, _reason = decide(_verdict(in_niche=False))
    assert accepted is False


def test_good_content_passes() -> None:
    accepted, reason = decide(_verdict())
    assert accepted is True
    assert reason is None


def test_min_score_is_configurable(settings: Settings) -> None:
    settings.ai_min_score = 90
    set_settings(settings)
    accepted, _ = decide(_verdict(funny_score=80))
    assert accepted is False


# ----------------------------------------------------------------------
# Caption
# ----------------------------------------------------------------------
def test_caption_falls_back_when_ai_disabled() -> None:
    result = writer.write("çok komik bir tweet")
    assert result.generated is False
    assert result.hashtags


def test_caption_fallback_truncates_long_text() -> None:
    result = writer.write("x" * 400)
    assert len(result.caption) <= 181
    assert result.caption.endswith("…")


def test_hashtags_are_normalised() -> None:
    tags = writer._normalise(["mizah", "#komik", "#Komik", " boş luk ", ""])
    assert tags[0] == "#mizah"
    assert tags.count("#komik") == 1
    assert "#boşluk" in tags


def test_hashtags_are_capped() -> None:
    assert len(writer._normalise([f"tag{i}" for i in range(50)])) == writer.MAX_HASHTAGS
