"""ffprobe interpretation.

ffprobe hands stills a nominal duration and frame rate. Believing it turns a
JPEG into a one-frame Reel, so image/video detection gets its own tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smauto.media.probe import FFmpegError, MediaInfo, extract_frame, probe, run_ffmpeg

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("probe")

    jpg = base / "still.jpg"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=800x600", "-frames:v", "1", str(jpg)])

    png = base / "still.png"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=800x600", "-frames:v", "1", str(png)])

    mp4 = base / "clip.mp4"
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x480:rate=30:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(mp4),
        ]
    )

    gif = base / "anim.gif"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=200x200:rate=10:duration=2", str(gif)])

    return {"jpg": jpg, "png": png, "mp4": mp4, "gif": gif}


def test_jpeg_is_an_image_not_a_one_frame_video(media: dict[str, Path]) -> None:
    info = probe(media["jpg"])
    assert info.is_image is True
    assert info.is_video is False
    assert info.duration_ms == 0


def test_png_is_an_image(media: dict[str, Path]) -> None:
    info = probe(media["png"])
    assert info.is_image is True
    assert info.is_video is False


def test_mp4_is_a_video(media: dict[str, Path]) -> None:
    info = probe(media["mp4"])
    assert info.is_video is True
    assert info.is_image is False
    assert 2500 <= info.duration_ms <= 3500
    assert (info.width, info.height) == (640, 480)


def test_animated_gif_counts_as_video(media: dict[str, Path]) -> None:
    info = probe(media["gif"])
    assert info.is_video is True


def test_probe_reports_missing_audio(media: dict[str, Path]) -> None:
    assert probe(media["mp4"]).has_audio is False


def test_probe_raises_on_garbage(tmp_path: Path) -> None:
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"definitely not a video")
    with pytest.raises(FFmpegError):
        probe(junk)


def test_extract_frame_from_video(media: dict[str, Path], tmp_path: Path) -> None:
    dest = extract_frame(media["mp4"], tmp_path / "frame.jpg")
    assert dest.exists()
    assert probe(dest).is_image is True


def test_media_info_aspect() -> None:
    info = MediaInfo(
        width=1920,
        height=1080,
        duration_ms=1000,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
        pix_fmt="yuv420p",
        profile="High",
        fps=30.0,
        size_bytes=1,
        format_name="mov,mp4",
    )
    assert abs(info.aspect - 16 / 9) < 0.01
    assert info.is_video is True
