"""Minimal Telegram Bot API client for invoice files and private text messages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


_API_BASE = "https://api.telegram.org"
_SUPPORTED_MIMETYPES = {"application/pdf", "image/jpeg", "image/png"}
_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


class TelegramError(Exception):
    """Raised when a Telegram Bot API call fails or returns invalid data."""


@dataclass(frozen=True)
class TelegramFile:
    """A supported file attached to a Telegram message."""

    file_id: str
    file_name: str
    mimetype: str
    duration_seconds: int | None = None


@dataclass(frozen=True)
class TelegramMessage:
    """A private Telegram message carrying a supported file or plain text."""

    update_id: int
    message_id: int
    chat_id: int
    sender_id: int
    sender_name: str
    caption: str
    file: TelegramFile | None = None
    voice: TelegramFile | None = None
    text: str = ""


class TelegramClient:
    """Poll a Telegram bot for invoice files and reply to senders."""

    def __init__(self, bot_token: str, *, request_timeout: float = 35.0) -> None:
        if not bot_token.strip():
            raise ValueError("Telegram bot token must not be empty.")
        if request_timeout <= 0:
            raise ValueError("Telegram request timeout must be positive.")
        self._token = bot_token.strip()
        self._request_timeout = request_timeout

    def get_updates(
        self, offset: int | None = None
    ) -> tuple[int | None, list[TelegramMessage]]:
        """Fetch pending updates.

        Returns:
            The offset to use on the next call (``None`` when there were no
            updates) and the private messages carrying supported invoice files
            or plain text. Other updates are skipped but still advance offset.
        """
        params: dict[str, Any] = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            params["offset"] = offset
        updates = self._call("getUpdates", params)
        if not isinstance(updates, list):
            raise TelegramError("Telegram returned an invalid updates list.")

        messages: list[TelegramMessage] = []
        last_update_id: int | None = None
        for update in updates:
            if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
                continue
            last_update_id = max(last_update_id or 0, update["update_id"])
            message = self._parse_message(update)
            if message:
                messages.append(message)
        next_offset = last_update_id + 1 if last_update_id is not None else None
        return next_offset, messages

    def set_webhook(self, url: str, secret_token: str) -> None:
        """Register the HTTPS webhook; Telegram echoes the secret in a header."""
        if not url.startswith("https://"):
            raise ValueError("Telegram webhook URL must use HTTPS.")
        if not 1 <= len(secret_token) <= 256:
            raise ValueError("Telegram webhook secret must be 1-256 characters.")
        self._call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": json.dumps(["message"]),
                "drop_pending_updates": "false",
            },
        )

    def delete_webhook(self) -> None:
        """Remove the webhook so getUpdates polling works again."""
        self._call("deleteWebhook", {"drop_pending_updates": "false"})

    def webhook_info(self) -> dict[str, Any]:
        """Return Telegram's webhook status (url, pending count, last error)."""
        info = self._call("getWebhookInfo", {})
        return info if isinstance(info, dict) else {}

    @staticmethod
    def parse_update(update: dict[str, Any]) -> TelegramMessage | None:
        """Parse a raw update into a private message, or ``None`` if unsupported."""
        if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
            return None
        return TelegramClient._parse_message(update)

    def download_file(self, file_id: str) -> bytes:
        """Download a file sent to the bot (Telegram limits bots to 20 MB)."""
        file_info = self._call("getFile", {"file_id": file_id})
        file_path = file_info.get("file_path") if isinstance(file_info, dict) else None
        if not isinstance(file_path, str) or not file_path:
            raise TelegramError("Telegram did not return a file path.")
        request = Request(f"{_API_BASE}/file/bot{self._token}/{file_path}", method="GET")
        try:
            with urlopen(request, timeout=self._request_timeout) as response:
                content = response.read(_MAX_DOWNLOAD_BYTES + 1)
        except (HTTPError, URLError, TimeoutError) as error:
            raise TelegramError("Telegram file download failed.") from error
        if not content or len(content) > _MAX_DOWNLOAD_BYTES:
            raise TelegramError("Telegram file is empty or too large.")
        return content

    def send_message(self, chat_id: int, text: str) -> None:
        """Send a plain text reply to a chat."""
        self._call("sendMessage", {"chat_id": chat_id, "text": text})

    def send_voice(self, chat_id: int, audio: bytes, filename: str = "reply.mp3") -> None:
        """Upload an MP3 voice reply to a Telegram private chat."""
        if not audio:
            raise ValueError("Voice reply must not be empty.")
        boundary = f"----rioclaro-{uuid4().hex}"
        fields = {
            "chat_id": str(chat_id),
            "voice": (filename, "audio/mpeg", audio),
        }
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"'.encode(),
                ]
            )
            if isinstance(value, tuple):
                file_name, mimetype, content = value
                chunks.extend(
                    [
                        f'; filename="{file_name}"\r\n'.encode(),
                        f"Content-Type: {mimetype}\r\n\r\n".encode(),
                        content,
                        b"\r\n",
                    ]
                )
            else:
                chunks.extend([b"\r\n\r\n", value.encode(), b"\r\n"])
        chunks.append(f"--{boundary}--\r\n".encode())
        request = Request(
            f"{_API_BASE}/bot{self._token}/sendVoice",
            data=b"".join(chunks),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        self._call_request(request, "sendVoice")

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        request = Request(
            f"{_API_BASE}/bot{self._token}/{method}",
            data=urlencode(params).encode(),
            method="POST",
        )
        return self._call_request(request, method)

    def _call_request(self, request: Request, method: str) -> Any:
        try:
            with urlopen(request, timeout=self._request_timeout) as response:
                payload = json.loads(response.read())
        except HTTPError as error:
            raise TelegramError(f"Telegram {method} returned HTTP {error.code}.") from error
        except (URLError, TimeoutError) as error:
            raise TelegramError(f"Telegram {method} could not be reached.") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise TelegramError(f"Telegram {method} returned invalid JSON.") from error
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise TelegramError(f"Telegram {method} was not successful.")
        return payload.get("result")

    @staticmethod
    def _parse_message(update: dict[str, Any]) -> TelegramMessage | None:
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if not isinstance(chat.get("id"), int) or not isinstance(sender.get("id"), int):
            return None

        telegram_file: TelegramFile | None = None
        document = message.get("document")
        photos = message.get("photo")
        voice_message = message.get("voice")
        voice_file: TelegramFile | None = None
        if isinstance(voice_message, dict) and voice_message.get("file_id"):
            voice_file = TelegramFile(
                file_id=str(voice_message["file_id"]),
                file_name=f"voice-{message.get('message_id')}.ogg",
                mimetype=str(voice_message.get("mime_type") or "audio/ogg"),
                duration_seconds=(
                    voice_message.get("duration")
                    if isinstance(voice_message.get("duration"), int)
                    else None
                ),
            )
        if isinstance(document, dict) and document.get("mime_type") in _SUPPORTED_MIMETYPES:
            telegram_file = TelegramFile(
                file_id=str(document.get("file_id", "")),
                file_name=str(document.get("file_name") or "invoice"),
                mimetype=document["mime_type"],
            )
        elif isinstance(photos, list) and photos:
            # Telegram sends several sizes of a photo; the last one is the largest.
            largest = photos[-1]
            telegram_file = TelegramFile(
                file_id=str(largest.get("file_id", "")),
                file_name=f"photo-{message.get('message_id')}.jpg",
                mimetype="image/jpeg",
            )
        if chat.get("type") != "private":
            return None
        if telegram_file and not telegram_file.file_id:
            return None
        text = message.get("text")
        if (
            not telegram_file
            and not voice_file
            and (not isinstance(text, str) or not text.strip())
        ):
            return None
        return TelegramMessage(
            update_id=update["update_id"],
            message_id=int(message.get("message_id", 0)),
            chat_id=chat["id"],
            sender_id=sender["id"],
            sender_name=str(sender.get("username") or sender.get("first_name") or sender["id"]),
            caption=str(message.get("caption") or ""),
            file=telegram_file,
            voice=voice_file,
            text=text.strip() if isinstance(text, str) else "",
        )
