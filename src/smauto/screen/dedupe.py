"""Perceptual-hash deduplication.

A unique index on (platform, source_id) only catches the same post twice. The
same meme reposted by a different account is the case that actually embarrasses
a page, and that needs image similarity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from smauto.db.repo import recent_phashes
from smauto.logging import get_logger
from smauto.media.probe import FFmpegError, extract_frame, probe

log = get_logger(__name__)

#: Hamming distance under which two 64-bit pHashes count as the same image.
#: 6 tolerates re-encoding, rescaling and light cropping without matching
#: unrelated images.
THRESHOLD = 6

#: Comparing against everything is unnecessary; recent history is what repeats.
LOOKBACK = 2000


@dataclass(slots=True)
class DupeResult:
    phash: str | None
    duplicate_of: int | None
    distance: int | None = None


def compute_phash(path: Path, *, workdir: Path | None = None) -> str | None:
    """Perceptual hash of an image, or of a representative frame of a video."""
    try:
        import imagehash
        from PIL import Image
    except ImportError:  # pragma: no cover - imagehash is a hard dependency
        return None

    target = path
    try:
        info = probe(path)
        if info.is_video:
            frames_dir = workdir or path.parent
            target = extract_frame(path, frames_dir / f"{path.stem}_phash.jpg")
    except FFmpegError as exc:
        log.debug("probe failed, hashing the file directly", path=str(path), error=str(exc)[:120])

    try:
        with Image.open(target) as im:
            return str(imagehash.phash(im.convert("RGB")))
    except Exception as exc:
        log.info("phash failed", path=str(target), error=str(exc)[:160])
        return None


def hamming(a: str, b: str) -> int:
    """Distance between two hex pHash strings."""
    if len(a) != len(b):
        raise ValueError("hash lengths differ")
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def find_duplicate(
    session: Session, phash: str, *, exclude_id: int | None = None, threshold: int = THRESHOLD
) -> tuple[int, int] | None:
    """Return (item_id, distance) of the closest match within `threshold`."""
    best: tuple[int, int] | None = None
    for item_id, other in recent_phashes(session, limit=LOOKBACK, exclude_id=exclude_id):
        try:
            distance = hamming(phash, other)
        except ValueError:
            continue
        if distance <= threshold and (best is None or distance < best[1]):
            best = (item_id, distance)
            if distance == 0:
                break
    return best


def check(
    session: Session, path: Path, *, exclude_id: int | None = None, workdir: Path | None = None
) -> DupeResult:
    phash = compute_phash(path, workdir=workdir)
    if phash is None:
        return DupeResult(phash=None, duplicate_of=None)

    match = find_duplicate(session, phash, exclude_id=exclude_id)
    if match is None:
        return DupeResult(phash=phash, duplicate_of=None)
    return DupeResult(phash=phash, duplicate_of=match[0], distance=match[1])


__all__ = [
    "LOOKBACK",
    "THRESHOLD",
    "DupeResult",
    "check",
    "compute_phash",
    "find_duplicate",
    "hamming",
]
