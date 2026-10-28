"""Instagram long-lived token rotation.

Long-lived tokens last 60 days. Nothing warns you when one lapses — the
pipeline simply stops publishing — so this runs weekly and shouts on Telegram
before it becomes a problem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from smauto.config import get_settings
from smauto.db.repo import get_token, log_event, save_token, utc
from smauto.logging import get_logger

log = get_logger(__name__)

REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
EXCHANGE_URL = "https://graph.instagram.com/access_token"

PROVIDER = "instagram"
#: Below this, start nagging.
WARN_DAYS = 14
#: Instagram refuses to refresh a token younger than 24 hours.
MIN_AGE_HOURS = 24


class TokenError(RuntimeError):
    pass


@dataclass(slots=True)
class TokenStatus:
    present: bool
    expires_at: datetime | None
    days_left: int | None

    @property
    def needs_attention(self) -> bool:
        return not self.present or (self.days_left is not None and self.days_left <= WARN_DAYS)


def status(session: Session) -> TokenStatus:
    settings = get_settings()
    row = get_token(session, PROVIDER)

    if row is None:
        # Fall back to the env-provided token; the expiry is simply unknown
        # until the first refresh writes it to the database.
        return TokenStatus(present=bool(settings.ig_access_token), expires_at=None, days_left=None)

    expires_at = utc(row.expires_at) if row.expires_at else None
    days = (expires_at - datetime.now(UTC)).days if expires_at else None
    return TokenStatus(present=bool(row.access_token), expires_at=expires_at, days_left=days)


def current_token(session: Session) -> str:
    """The freshest token available: database first, environment as seed."""
    row = get_token(session, PROVIDER)
    if row and row.access_token:
        return row.access_token
    settings = get_settings()
    if not settings.ig_access_token:
        raise TokenError("no Instagram access token available")
    return settings.ig_access_token


def refresh(session: Session) -> TokenStatus:
    """Exchange the current long-lived token for a fresh 60-day one."""
    token = current_token(session)
    settings = get_settings()

    try:
        with httpx.Client(timeout=max(settings.http_timeout_seconds, 60)) as client:
            resp = client.get(
                REFRESH_URL, params={"grant_type": "ig_refresh_token", "access_token": token}
            )
            body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TokenError(f"refresh request failed: {exc!r}") from exc

    if resp.status_code >= 400 or "error" in body:
        error = body.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else body
        raise TokenError(f"refresh rejected: {message}")

    access_token = body.get("access_token")
    expires_in = int(body.get("expires_in") or 0)
    if not access_token:
        raise TokenError(f"refresh returned no token: {body}")

    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in or 60 * 24 * 3600)
    save_token(session, PROVIDER, access_token=access_token, expires_at=expires_at)
    log_event(
        session,
        stage="tokens",
        message="instagram token refreshed",
        payload={"expires_at": expires_at.isoformat(), "days": expires_in // 86400},
    )
    return status(session)


def exchange_short_lived(session: Session, short_lived_token: str) -> TokenStatus:
    """One-off: turn the token from the OAuth redirect into a 60-day token."""
    settings = get_settings()
    if not settings.ig_app_secret:
        raise TokenError("IG_APP_SECRET is required to exchange a short-lived token")

    try:
        with httpx.Client(timeout=max(settings.http_timeout_seconds, 60)) as client:
            resp = client.get(
                EXCHANGE_URL,
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": settings.ig_app_secret,
                    "access_token": short_lived_token,
                },
            )
            body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TokenError(f"exchange request failed: {exc!r}") from exc

    if resp.status_code >= 400 or "error" in body:
        raise TokenError(f"exchange rejected: {body}")

    access_token = body.get("access_token")
    expires_in = int(body.get("expires_in") or 60 * 24 * 3600)
    if not access_token:
        raise TokenError(f"exchange returned no token: {body}")

    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    save_token(session, PROVIDER, access_token=access_token, expires_at=expires_at)
    return status(session)


__all__ = [
    "MIN_AGE_HOURS",
    "PROVIDER",
    "WARN_DAYS",
    "TokenError",
    "TokenStatus",
    "current_token",
    "exchange_short_lived",
    "refresh",
    "status",
]
