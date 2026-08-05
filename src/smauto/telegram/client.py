"""A small synchronous Telegram Bot API client.

Only the handful of methods this project needs, so the whole pipeline can stay
synchronous instead of dragging in an async bot framework.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from smauto.config import get_settings
from smauto.logging import get_logger

log = get_logger(__name__)

API_ROOT = "https://api.telegram.org"

#: Bot API upload ceiling for regular bots.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
#: Caption ceiling on media messages; longer text is sent as a follow-up message.
MAX_CAPTION_CHARS = 1024
MAX_MESSAGE_CHARS = 4096


class TelegramError(RuntimeError):
    def __init__(self, method: str, description: str, error_code: int | None = None) -> None:
        super().__init__(f"{method}: {description}")
        self.method = method
        self.description = description
        self.error_code = error_code


@dataclass(slots=True)
class InlineButton:
    text: str
    callback_data: str


def keyboard(rows: list[list[InlineButton]]) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": b.text, "callback_data": b.callback_data} for b in row] for row in rows
        ]
    }


class TelegramClient:
    def __init__(self, token: str | None = None, *, timeout: int | None = None) -> None:
        settings = get_settings()
        self.token = token or settings.telegram_bot_token
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set")
        self._timeout = timeout or max(settings.http_timeout_seconds, 30)
        self._client = httpx.Client(timeout=self._timeout)

    # -- plumbing -----------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TelegramClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _call(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        files: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{API_ROOT}/bot{self.token}/{method}"
        data = {k: v for k, v in (payload or {}).items() if v is not None}
        # Nested structures must be JSON-encoded when sent as form fields.
        for key, value in list(data.items()):
            if isinstance(value, (dict, list)):
                data[key] = json.dumps(value, ensure_ascii=False)

        try:
            resp = self._client.post(url, data=data, files=files, timeout=timeout or self._timeout)
        except httpx.HTTPError as exc:
            raise TelegramError(method, f"transport error: {exc!r}") from exc

        try:
            body = resp.json()
        except ValueError as exc:
            raise TelegramError(method, f"non-JSON response (HTTP {resp.status_code})") from exc

        if not body.get("ok"):
            raise TelegramError(
                method, str(body.get("description") or "unknown error"), body.get("error_code")
            )
        return body.get("result")

    # -- methods ------------------------------------------------------
    def get_me(self) -> dict[str, Any]:
        result: dict[str, Any] = self._call("getMe")
        return result

    def get_updates(
        self, offset: int | None = None, *, limit: int = 50, timeout_s: int = 0
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._call(
            "getUpdates",
            {
                "offset": offset,
                "limit": limit,
                "timeout": timeout_s,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=self._timeout + timeout_s,
        )
        return result

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        reply_to: int | None = None,
        disable_preview: bool = True,
    ) -> dict[str, Any]:
        result: dict[str, Any] = self._call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text[:MAX_MESSAGE_CHARS],
                "reply_markup": reply_markup,
                "reply_to_message_id": reply_to,
                "link_preview_options": {"is_disabled": disable_preview},
            },
        )
        return result

    def send_video(
        self,
        chat_id: int,
        path: Path,
        *,
        caption: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        width: int | None = None,
        height: int | None = None,
        duration_s: int | None = None,
    ) -> dict[str, Any]:
        with path.open("rb") as fh:
            result: dict[str, Any] = self._call(
                "sendVideo",
                {
                    "chat_id": chat_id,
                    "caption": _clip_caption(caption),
                    "reply_markup": reply_markup,
                    "width": width,
                    "height": height,
                    "duration": duration_s,
                    "supports_streaming": True,
                },
                files={"video": (path.name, fh, "video/mp4")},
                timeout=max(self._timeout, 300),
            )
        return result

    def send_photo(
        self,
        chat_id: int,
        path: Path,
        *,
        caption: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with path.open("rb") as fh:
            result: dict[str, Any] = self._call(
                "sendPhoto",
                {
                    "chat_id": chat_id,
                    "caption": _clip_caption(caption),
                    "reply_markup": reply_markup,
                },
                files={"photo": (path.name, fh, "image/jpeg")},
                timeout=max(self._timeout, 180),
            )
        return result

    def send_document(
        self,
        chat_id: int,
        path: Path,
        *,
        caption: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with path.open("rb") as fh:
            result: dict[str, Any] = self._call(
                "sendDocument",
                {
                    "chat_id": chat_id,
                    "caption": _clip_caption(caption),
                    "reply_markup": reply_markup,
                },
                files={"document": (path.name, fh, "application/octet-stream")},
                timeout=max(self._timeout, 300),
            )
        return result

    def edit_message_caption(
        self,
        chat_id: int,
        message_id: int,
        caption: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        self._call(
            "editMessageCaption",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "caption": _clip_caption(caption),
                "reply_markup": reply_markup,
            },
        )

    def edit_reply_markup(
        self, chat_id: int, message_id: int, reply_markup: dict[str, Any] | None
    ) -> None:
        self._call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
        )

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def download_file(self, file_id: str, dest: Path) -> Path:
        info = self._call("getFile", {"file_id": file_id})
        file_path = info.get("file_path")
        if not file_path:
            raise TelegramError("getFile", "response had no file_path")

        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"{API_ROOT}/file/bot{self.token}/{file_path}"
        with self._client.stream("GET", url, timeout=max(self._timeout, 300)) as resp:
            if resp.status_code != 200:
                raise TelegramError("download", f"HTTP {resp.status_code}")
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 16):
                    fh.write(chunk)
        return dest


def _clip_caption(caption: str | None) -> str | None:
    if caption is None:
        return None
    return caption if len(caption) <= MAX_CAPTION_CHARS else caption[: MAX_CAPTION_CHARS - 1] + "…"


__all__ = [
    "MAX_CAPTION_CHARS",
    "MAX_MESSAGE_CHARS",
    "MAX_UPLOAD_BYTES",
    "InlineButton",
    "TelegramClient",
    "TelegramError",
    "keyboard",
]
