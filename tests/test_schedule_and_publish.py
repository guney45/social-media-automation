from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from smauto.config import Settings, set_settings
from smauto.db import states
from smauto.db.models import Item
from smauto.publish import instagram
from smauto.schedule import planner


def make_item(session: Session, idx: int, status: str = states.APPROVED) -> Item:
    item = Item(
        source_platform="x",
        source_url=f"https://x.com/a/status/{idx}",
        source_id=str(idx),
        status=status,
    )
    session.add(item)
    session.flush()
    return item


# ----------------------------------------------------------------------
# Planner
# ----------------------------------------------------------------------
def test_next_slot_is_in_the_future(session: Session, settings: Settings) -> None:
    now = datetime(2026, 3, 14, 10, 0, tzinfo=UTC)
    slot = planner.next_free_slot(session, now=now)
    assert slot is not None
    assert slot > now


def test_slots_do_not_collide(session: Session) -> None:
    now = datetime(2026, 3, 14, 6, 0, tzinfo=UTC)
    chosen = []
    for i in range(3):
        item = make_item(session, i)
        slot = planner.schedule(session, item, now=now)
        item.status = states.SCHEDULED
        session.flush()
        chosen.append(slot)
    assert len(set(chosen)) == 3


def test_daily_cap_pushes_to_the_next_day(session: Session, settings: Settings) -> None:
    settings.max_per_day = 2
    set_settings(settings)
    now = datetime(2026, 3, 14, 6, 0, tzinfo=UTC)

    slots = []
    for i in range(4):
        item = make_item(session, i)
        slot = planner.schedule(session, item, now=now)
        item.status = states.SCHEDULED
        session.flush()
        assert slot is not None
        slots.append(slot.astimezone(settings.tz).date())

    assert slots[0] == slots[1]
    assert slots[2] == slots[3]
    assert slots[2] > slots[0]


def test_due_for_publish_only_returns_ripe_items(session: Session) -> None:
    now = datetime(2026, 3, 14, 20, 0, tzinfo=UTC)

    ready = make_item(session, 1, status=states.SCHEDULED)
    ready.scheduled_for = datetime(2026, 3, 14, 15, 0, tzinfo=UTC)
    later = make_item(session, 2, status=states.SCHEDULED)
    later.scheduled_for = datetime(2026, 3, 15, 15, 0, tzinfo=UTC)
    session.flush()

    due = planner.due_for_publish(session, now=now)
    assert [i.id for i in due] == [ready.id]


# ----------------------------------------------------------------------
# Instagram publishing
# ----------------------------------------------------------------------
class _Recorder:
    """Mock Graph API: container → poll → publish."""

    def __init__(self, statuses: list[str], *, publish_ok: bool = True) -> None:
        self.statuses = list(statuses)
        self.publish_ok = publish_ok
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(f"{request.method} {url.split('?')[0].rsplit('/', 1)[-1]}")

        if request.method == "POST" and url.endswith("/media"):
            return httpx.Response(200, json={"id": "container-1"})
        if request.method == "POST" and url.endswith("/media_publish"):
            if not self.publish_ok:
                return httpx.Response(400, json={"error": {"message": "nope", "code": 100}})
            return httpx.Response(200, json={"id": "media-9"})
        if request.method == "GET" and "container-1" in url:
            status = self.statuses.pop(0) if self.statuses else "FINISHED"
            return httpx.Response(200, json={"status_code": status, "status": "detail"})
        if request.method == "GET" and "media-9" in url:
            return httpx.Response(200, json={"permalink": "https://instagram.com/p/xyz"})
        return httpx.Response(404, json={"error": {"message": "unknown route"}})


@pytest.fixture
def ig_settings(settings: Settings) -> Settings:
    settings.ig_user_id = "1784"
    settings.ig_access_token = "token"
    set_settings(settings)
    return settings


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler: _Recorder) -> None:
    original = httpx.Client

    def factory(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(instagram.httpx, "Client", factory)


def test_publish_reel_walks_the_three_steps(
    monkeypatch: pytest.MonkeyPatch, ig_settings: Settings
) -> None:
    handler = _Recorder(["IN_PROGRESS", "FINISHED"])
    monkeypatch.setattr(instagram, "POLL_INTERVAL_S", 0)
    _patch_transport(monkeypatch, handler)

    result = instagram.publish_reel("https://cdn/x.mp4", "caption")

    assert result.media_id == "media-9"
    assert result.permalink == "https://instagram.com/p/xyz"
    assert handler.calls[0] == "POST media"
    assert "POST media_publish" in handler.calls


def test_container_error_is_not_retryable(
    monkeypatch: pytest.MonkeyPatch, ig_settings: Settings
) -> None:
    handler = _Recorder(["ERROR"])
    monkeypatch.setattr(instagram, "POLL_INTERVAL_S", 0)
    _patch_transport(monkeypatch, handler)

    with pytest.raises(instagram.InstagramError) as exc:
        instagram.publish_reel("https://cdn/x.mp4", "caption")
    assert exc.value.retryable is False


def test_container_timeout_raises(monkeypatch: pytest.MonkeyPatch, ig_settings: Settings) -> None:
    handler = _Recorder(["IN_PROGRESS"] * 50)
    monkeypatch.setattr(instagram, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(instagram, "POLL_TIMEOUT_S", 0)
    _patch_transport(monkeypatch, handler)

    with pytest.raises(instagram.InstagramError, match="still"):
        instagram.publish_reel("https://cdn/x.mp4", "caption")


def test_bad_token_is_not_retryable(monkeypatch: pytest.MonkeyPatch, ig_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "expired", "code": 190}})

    _patch_transport(monkeypatch, handler)  # type: ignore[arg-type]

    with pytest.raises(instagram.InstagramError) as exc:
        instagram.publish_image("https://cdn/x.jpg", "caption")
    assert exc.value.retryable is False


def test_carousel_requires_two_to_ten_items(ig_settings: Settings) -> None:
    with pytest.raises(instagram.InstagramError, match="between 2 and 10"):
        instagram.publish_carousel(["https://cdn/a.jpg"], "caption")
