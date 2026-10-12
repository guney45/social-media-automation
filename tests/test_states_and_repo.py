from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from smauto.db import states
from smauto.db.models import Item
from smauto.db.repo import (
    add_asset,
    block,
    claim_retry,
    due_items,
    is_blocked,
    kv_get,
    kv_set,
    mark_failed,
    status_counts,
    transition,
)


def make_item(session: Session, **kwargs) -> Item:  # type: ignore[no-untyped-def]
    defaults = {
        "source_platform": "x",
        "source_url": "https://x.com/a/status/1",
        "source_id": "1",
        "status": states.QUEUED,
    }
    item = Item(**{**defaults, **kwargs})
    session.add(item)
    session.flush()
    return item


def test_legal_transition(session: Session) -> None:
    item = make_item(session)
    transition(session, item, states.FETCHING)
    assert item.status == states.FETCHING


def test_illegal_transition_is_refused(session: Session) -> None:
    item = make_item(session)
    with pytest.raises(states.InvalidTransitionError):
        transition(session, item, states.PUBLISHED)


def test_rejected_is_terminal(session: Session) -> None:
    item = make_item(session, status=states.REJECTED)
    with pytest.raises(states.InvalidTransitionError):
        transition(session, item, states.QUEUED)


def test_every_state_has_a_turkish_label() -> None:
    assert set(states.LABELS) == states.ALL


def test_transition_targets_are_all_known_states() -> None:
    for source, targets in states.TRANSITIONS.items():
        assert source in states.ALL
        assert targets <= states.ALL


def test_transition_writes_an_event(session: Session) -> None:
    from smauto.db.models import Event

    item = make_item(session)
    transition(session, item, states.FETCHING, stage="fetching", message="hello")
    events = session.query(Event).filter(Event.item_id == item.id).all()
    assert any(e.message == "hello" for e in events)


def test_mark_failed_backs_off_then_gives_up(session: Session) -> None:
    item = make_item(session, status=states.FETCHING)

    assert mark_failed(session, item, stage="fetching", error="boom", max_attempts=3) is True
    assert item.attempts == 1
    assert item.next_attempt_at is not None
    assert item.status == states.FAILED

    transition(session, item, states.FETCHING)
    assert mark_failed(session, item, stage="fetching", error="boom", max_attempts=3) is True

    transition(session, item, states.FETCHING)
    assert mark_failed(session, item, stage="fetching", error="boom", max_attempts=3) is False
    assert item.next_attempt_at is None


def test_claim_retry_waits_for_the_backoff(session: Session) -> None:
    item = make_item(session, status=states.FETCHING)
    mark_failed(session, item, stage="fetching", error="boom", max_attempts=3)

    assert claim_retry(session, item) is False  # still backing off
    assert claim_retry(session, item, now=datetime.now(UTC) + timedelta(hours=1)) is True
    assert item.status == states.QUEUED


def test_due_items_skips_items_waiting_on_backoff(session: Session) -> None:
    ready = make_item(session, source_id="ready", source_url="u1")
    waiting = make_item(session, source_id="waiting", source_url="u2", status=states.FETCHING)
    mark_failed(session, waiting, stage="fetching", error="x", max_attempts=3)

    ids = {i.id for i in due_items(session)}
    assert ready.id in ids
    assert waiting.id not in ids


def test_blocklist_is_case_insensitive(session: Session) -> None:
    block(session, kind="author", value="SpamUser")
    assert is_blocked(session, kind="author", value="spamuser") is not None
    assert is_blocked(session, kind="author", value="someone") is None


def test_blocklist_is_idempotent(session: Session) -> None:
    a = block(session, kind="author", value="dup")
    b = block(session, kind="author", value="dup")
    assert a.id == b.id


def test_add_asset_replaces_same_variant(session: Session) -> None:
    item = make_item(session)
    add_asset(session, item, kind="render", variant="reel", local_path="/a.mp4")
    add_asset(session, item, kind="render", variant="reel", local_path="/b.mp4")
    session.flush()
    reels = [a for a in item.assets if a.variant == "reel"]
    assert len(reels) == 1
    assert reels[0].local_path == "/b.mp4"


def test_kv_roundtrip(session: Session) -> None:
    assert kv_get(session, "missing") is None
    kv_set(session, "offset", "12")
    assert kv_get(session, "offset") == "12"
    kv_set(session, "offset", "13")
    assert kv_get(session, "offset") == "13"


def test_status_counts(session: Session) -> None:
    make_item(session, source_id="a", source_url="a")
    make_item(session, source_id="b", source_url="b")
    make_item(session, source_id="c", source_url="c", status=states.REJECTED)
    counts = status_counts(session)
    assert counts[states.QUEUED] == 2
    assert counts[states.REJECTED] == 1
