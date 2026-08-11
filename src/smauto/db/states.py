"""Item status values and the legal transitions between them.

Enforcing this in one place is what keeps the pipeline honest: every stage
either advances the item or records why it could not.
"""

from __future__ import annotations

from typing import Final

QUEUED: Final = "queued"
FETCHING: Final = "fetching"
FETCHED: Final = "fetched"
NEEDS_MANUAL: Final = "needs_manual"
SCREENING: Final = "screening"
SCREENED: Final = "screened"
RENDERING: Final = "rendering"
RENDERED: Final = "rendered"
CAPTIONING: Final = "captioning"
AWAITING_APPROVAL: Final = "awaiting_approval"
APPROVED: Final = "approved"
SCHEDULED: Final = "scheduled"
PUBLISHING: Final = "publishing"
PUBLISHED: Final = "published"
REJECTED: Final = "rejected"
FAILED: Final = "failed"

ALL: Final[frozenset[str]] = frozenset(
    {
        QUEUED,
        FETCHING,
        FETCHED,
        NEEDS_MANUAL,
        SCREENING,
        SCREENED,
        RENDERING,
        RENDERED,
        CAPTIONING,
        AWAITING_APPROVAL,
        APPROVED,
        SCHEDULED,
        PUBLISHING,
        PUBLISHED,
        REJECTED,
        FAILED,
    }
)

#: Terminal states: nothing moves out of these.
TERMINAL: Final[frozenset[str]] = frozenset({PUBLISHED, REJECTED})

#: States a `process` run should pick up.
ACTIVE: Final[frozenset[str]] = frozenset({QUEUED, FETCHED, SCREENED, RENDERED, FAILED})

TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    QUEUED: frozenset({FETCHING, REJECTED, FAILED}),
    FETCHING: frozenset({FETCHED, NEEDS_MANUAL, REJECTED, FAILED}),
    NEEDS_MANUAL: frozenset({FETCHED, REJECTED, FAILED}),
    FETCHED: frozenset({SCREENING, REJECTED, FAILED}),
    SCREENING: frozenset({SCREENED, REJECTED, FAILED}),
    SCREENED: frozenset({RENDERING, REJECTED, FAILED}),
    RENDERING: frozenset({RENDERED, REJECTED, FAILED}),
    RENDERED: frozenset({CAPTIONING, REJECTED, FAILED}),
    CAPTIONING: frozenset({AWAITING_APPROVAL, REJECTED, FAILED}),
    AWAITING_APPROVAL: frozenset({APPROVED, REJECTED, SCREENED}),
    APPROVED: frozenset({SCHEDULED, REJECTED, PUBLISHING}),
    SCHEDULED: frozenset({PUBLISHING, REJECTED, APPROVED}),
    PUBLISHING: frozenset({PUBLISHED, REJECTED, FAILED, SCHEDULED}),
    PUBLISHED: frozenset({REJECTED}),  # only via /unpublish
    REJECTED: frozenset(),
    FAILED: frozenset({QUEUED, FETCHING, SCREENING, RENDERING, CAPTIONING, PUBLISHING, REJECTED}),
}

#: Which state a failure inside a stage should retry from.
RETRY_FROM: Final[dict[str, str]] = {
    FETCHING: QUEUED,
    SCREENING: FETCHED,
    RENDERING: SCREENED,
    CAPTIONING: RENDERED,
    PUBLISHING: SCHEDULED,
}

#: Human-readable Turkish labels, used in Telegram messages.
LABELS: Final[dict[str, str]] = {
    QUEUED: "kuyrukta",
    FETCHING: "indiriliyor",
    FETCHED: "indirildi",
    NEEDS_MANUAL: "dosya bekleniyor",
    SCREENING: "eleniyor",
    SCREENED: "elendi",
    RENDERING: "çiziliyor",
    RENDERED: "çizildi",
    CAPTIONING: "caption yazılıyor",
    AWAITING_APPROVAL: "onay bekliyor",
    APPROVED: "onaylandı",
    SCHEDULED: "zamanlandı",
    PUBLISHING: "yayınlanıyor",
    PUBLISHED: "yayınlandı",
    REJECTED: "reddedildi",
    FAILED: "hata",
}


class InvalidTransitionError(ValueError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"illegal status transition: {current} -> {target}")
        self.current = current
        self.target = target


def can_transition(current: str, target: str) -> bool:
    if target not in ALL:
        return False
    if current == target:
        return True
    return target in TRANSITIONS.get(current, frozenset())


__all__ = [
    "ACTIVE",
    "ALL",
    "APPROVED",
    "AWAITING_APPROVAL",
    "CAPTIONING",
    "FAILED",
    "FETCHED",
    "FETCHING",
    "LABELS",
    "NEEDS_MANUAL",
    "PUBLISHED",
    "PUBLISHING",
    "QUEUED",
    "REJECTED",
    "RENDERED",
    "RENDERING",
    "RETRY_FROM",
    "SCHEDULED",
    "SCREENED",
    "SCREENING",
    "TERMINAL",
    "TRANSITIONS",
    "InvalidTransitionError",
    "can_transition",
]
