"""Compose a Reels-ready MP4 from source video + tweet card.

Everything about the encode is pinned deliberately: Instagram silently rejects
or downgrades video that is not H.264 High/yuv420p with an audio track, and the
Reels tab only accepts 9:16 clips between 5 and 90 seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from smauto.logging import get_logger
from smauto.media.probe import MediaInfo, probe, run_ffmpeg
from smauto.render.layout import REEL, STORY, Frame, Placement, compose

log = get_logger(__name__)

#: Reels tab eligibility window. Outside it, Instagram publishes a plain video post.
MIN_DURATION_S = 5.0
MAX_DURATION_S = 90.0
#: Looping a 2-second clip to exactly 5.0s often lands just under after rounding.
LOOP_TARGET_S = 6.0

BLUR_SIGMA = 30
BACKGROUND_DIM = -0.15
FPS = 30


@dataclass(slots=True)
class VideoRenderResult:
    path: Path
    width: int
    height: int
    duration_ms: int
    notes: list[str] = field(default_factory=list)


def render_reel(
    source: Path,
    card: Path | None,
    dest: Path,
    *,
    frame: Frame = REEL,
    follow: Path | None = None,
) -> VideoRenderResult:
    """Build `dest` from `source` video with `card` overlaid above it.

    `follow` is a small "follow me" card overlaid below the media, when there
    is room for it — see `compose()` for how that room is decided.
    """
    info = probe(source)
    if info.width <= 0 or info.height <= 0:
        raise ValueError(f"source has no usable dimensions: {source}")

    notes: list[str] = []
    loops, duration_s = _duration_plan(info, notes)

    card_size = _png_size(card) if card else None
    follow_size = _png_size(follow) if follow else None
    placement = compose(
        frame=frame,
        card_size=card_size,
        media_size=(info.width, info.height),
        follow_size=follow_size,
    )
    if not placement.fits():  # pragma: no cover - compose() clamps, this is a guard
        raise ValueError("layout produced boxes outside the safe area")

    dest.parent.mkdir(parents=True, exist_ok=True)
    args = _build_args(
        source=source,
        card=card if card_size else None,
        follow=follow if placement.follow else None,
        dest=dest,
        info=info,
        placement=placement,
        loops=loops,
        duration_s=duration_s,
    )
    run_ffmpeg(args)

    out = probe(dest)
    _assert_instagram_safe(out)
    return VideoRenderResult(
        path=dest,
        width=out.width,
        height=out.height,
        duration_ms=out.duration_ms,
        notes=notes,
    )


def render_story(source: Path, card: Path | None, dest: Path) -> VideoRenderResult:
    return render_reel(source, card, dest, frame=STORY)


def _duration_plan(info: MediaInfo, notes: list[str]) -> tuple[int, float]:
    """Return (extra input loops, output duration in seconds)."""
    src_s = info.duration_ms / 1000 if info.duration_ms else 0.0
    if src_s <= 0:
        return 0, LOOP_TARGET_S

    if src_s > MAX_DURATION_S:
        notes.append(f"Video {src_s:.0f} sn'ydi, Reels için {MAX_DURATION_S:.0f} sn'ye kısaltıldı.")
        return 0, MAX_DURATION_S

    if src_s < MIN_DURATION_S:
        loops = 0
        total = src_s
        while total < LOOP_TARGET_S and loops < 30:
            loops += 1
            total = src_s * (loops + 1)
        notes.append(
            f"Video {src_s:.1f} sn'ydi, Reels'in 5 sn alt sınırı için {loops + 1}x döngülendi."
        )
        return loops, min(total, MAX_DURATION_S)

    return 0, src_s


def _build_args(
    *,
    source: Path,
    card: Path | None,
    follow: Path | None,
    dest: Path,
    info: MediaInfo,
    placement: Placement,
    loops: int,
    duration_s: float,
) -> list[str]:
    frame = placement.frame
    media = placement.media
    inputs: list[str] = []

    if loops:
        inputs += ["-stream_loop", str(loops)]
    inputs += ["-i", str(source)]

    card_index: int | None = None
    if card is not None and placement.card is not None:
        card_index = len(_input_indices(inputs))
        inputs += ["-i", str(card)]

    follow_index: int | None = None
    if follow is not None and placement.follow is not None:
        follow_index = len(_input_indices(inputs))
        inputs += ["-i", str(follow)]

    silent_index: int | None = None
    if not info.has_audio:
        silent_index = len(_input_indices(inputs))
        inputs += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

    steps: list[str] = [
        f"[0:v]scale={frame.width}:{frame.height}:force_original_aspect_ratio=increase,"
        f"crop={frame.width}:{frame.height},gblur=sigma={BLUR_SIGMA},"
        f"eq=brightness={BACKGROUND_DIM},setsar=1[bg]"
    ]

    last = "bg"
    if media is not None:
        steps.append(f"[0:v]scale={media.width}:{media.height}:flags=lanczos,setsar=1[fg]")
        steps.append(f"[{last}][fg]overlay={media.x}:{media.y}[v1]")
        last = "v1"

    if card_index is not None and placement.card is not None:
        box = placement.card
        steps.append(f"[{card_index}:v]scale={box.width}:{box.height}:flags=lanczos[card]")
        steps.append(f"[{last}][card]overlay={box.x}:{box.y}[v2]")
        last = "v2"

    if follow_index is not None and placement.follow is not None:
        box = placement.follow
        steps.append(f"[{follow_index}:v]scale={box.width}:{box.height}:flags=lanczos[follow]")
        steps.append(f"[{last}][follow]overlay={box.x}:{box.y}[v3]")
        last = "v3"

    steps.append(f"[{last}]format=yuv420p[vout]")

    audio_map: str
    if info.has_audio:
        steps.append("[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100:async=1[aout]")
        audio_map = "[aout]"
    else:
        assert silent_index is not None
        audio_map = f"{silent_index}:a"

    return [
        *inputs,
        "-filter_complex",
        ";".join(steps),
        "-map",
        "[vout]",
        "-map",
        audio_map,
        "-t",
        f"{duration_s:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        # Bound the bitrate: Instagram re-encodes above ~5 Mbps anyway, and it
        # keeps a 90 s reel near Telegram's 50 MB bot upload limit.
        "-maxrate",
        "5M",
        "-bufsize",
        "10M",
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        "-g",
        str(FPS * 2),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(dest),
    ]


def _input_indices(args: list[str]) -> list[int]:
    return [i for i, a in enumerate(args) if a == "-i"]


def _png_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.width, im.height


def _assert_instagram_safe(info: MediaInfo) -> None:
    """Catch the encoder settings Instagram rejects, before we ever upload."""
    problems: list[str] = []
    if info.pix_fmt != "yuv420p":
        problems.append(f"pix_fmt is {info.pix_fmt}, Instagram needs yuv420p")
    if info.video_codec != "h264":
        problems.append(f"video codec is {info.video_codec}, Instagram needs h264")
    if not info.has_audio:
        problems.append("no audio stream")
    if info.width % 2 or info.height % 2:
        problems.append(f"odd dimensions {info.width}x{info.height}")
    if problems:
        raise ValueError("rendered video is not Instagram-safe: " + "; ".join(problems))


__all__ = [
    "FPS",
    "LOOP_TARGET_S",
    "MAX_DURATION_S",
    "MIN_DURATION_S",
    "VideoRenderResult",
    "render_reel",
    "render_story",
]
