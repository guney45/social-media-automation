"""Thin wrappers around ffmpeg/ffprobe."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from smauto.logging import get_logger

log = get_logger(__name__)


class FFmpegError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FFmpegError("ffmpeg not found on PATH (install it, or run `smauto doctor`)")
    return path


def ffprobe_bin() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise FFmpegError("ffprobe not found on PATH (install ffmpeg, or run `smauto doctor`)")
    return path


#: ffprobe reports a nominal duration and frame rate for stills, so duration
#: alone cannot tell an image from a video. The container/codec can.
IMAGE_FORMATS = frozenset(
    {"image2", "image2pipe", "png_pipe", "jpeg_pipe", "mjpeg", "webp_pipe", "bmp_pipe", "tiff_pipe"}
)
IMAGE_CODECS = frozenset({"mjpeg", "png", "webp", "bmp", "tiff", "jpeg2000"})


@dataclass(slots=True)
class MediaInfo:
    width: int
    height: int
    duration_ms: int
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None
    pix_fmt: str | None
    profile: str | None
    fps: float | None
    size_bytes: int
    format_name: str | None = None
    frame_count: int | None = None

    @property
    def is_image(self) -> bool:
        """True for stills — including the ones ffprobe gives a fake duration."""
        if self.frame_count is not None and self.frame_count > 1:
            return False  # animated gif/webp: treat as video
        if self.format_name and any(part in IMAGE_FORMATS for part in self.format_name.split(",")):
            return True
        return self.video_codec in IMAGE_CODECS

    @property
    def is_video(self) -> bool:
        return self.video_codec is not None and not self.is_image

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0


def probe(path: Path) -> MediaInfo:
    """Read stream metadata. Raises FFmpegError when the file is unreadable."""
    cmd = [
        ffprobe_bin(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed for {path.name}: {proc.stderr.strip()[:400]}")

    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise FFmpegError(f"no video/image stream in {path.name}")

    duration_s = _first_float(fmt.get("duration"), video.get("duration"))
    info = MediaInfo(
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        duration_ms=round((duration_s or 0.0) * 1000),
        has_audio=audio is not None,
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name") if audio else None,
        pix_fmt=video.get("pix_fmt"),
        profile=video.get("profile"),
        fps=_parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        size_bytes=int(fmt.get("size") or (path.stat().st_size if path.exists() else 0)),
        format_name=fmt.get("format_name"),
        frame_count=_first_int(video.get("nb_frames")),
    )

    # A still that claims a duration would otherwise be composed as a one-frame
    # Reel; zero it so every consumer sees an image.
    if info.is_image:
        info.duration_ms = 0
    return info


def run_ffmpeg(args: list[str], *, timeout: int = 900) -> None:
    """Run ffmpeg with `-y -hide_banner`, raising on failure."""
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", *args]
    log.debug("ffmpeg", cmd=" ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed: {proc.stderr.strip()[:1200]}")


def extract_frame(video: Path, dest: Path, *, at_seconds: float = 1.0) -> Path:
    """Grab a still frame, used for thumbnails and perceptual hashing."""
    info = probe(video)
    ts = min(at_seconds, max(0.0, info.duration_ms / 1000 / 2))
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-ss", f"{ts:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(dest)])
    if not dest.exists():
        raise FFmpegError(f"could not extract a frame from {video.name}")
    return dest


def _first_float(*values: object) -> float | None:
    for v in values:
        try:
            if v is not None:
                return float(str(v))
        except (TypeError, ValueError):
            continue
    return None


def _first_int(*values: object) -> int | None:
    for v in values:
        if v is None:
            continue
        try:
            return int(str(v))
        except (TypeError, ValueError):
            continue
    return None


def _parse_fps(raw: object) -> float | None:
    if not isinstance(raw, str) or "/" not in raw:
        return None
    num, _, den = raw.partition("/")
    try:
        d = float(den)
        return float(num) / d if d else None
    except ValueError:
        return None


__all__ = [
    "FFmpegError",
    "MediaInfo",
    "extract_frame",
    "ffmpeg_bin",
    "ffprobe_bin",
    "probe",
    "run_ffmpeg",
]
