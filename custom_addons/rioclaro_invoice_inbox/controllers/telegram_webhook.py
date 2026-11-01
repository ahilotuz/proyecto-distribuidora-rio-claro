"""Public Telegram webhook endpoint: authenticate, enqueue, acknowledge."""

from __future__ import annotations

import hmac
import json
import logging
import os

from odoo import http
from odoo.http import Response, request

_logger = logging.getLogger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
MAX_BODY_BYTES = 256 * 1024


class TelegramWebhook(http.Controller):
    """Receive Telegram updates and hand them to the processing queue."""

    @http.route(
        "/rioclaro/telegram/webhook",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def receive(self) -> Response:
        """Validate the secret header, enqueue the update and answer 200 fast.

        Returns 403 for a missing or wrong secret (or when webhook mode is not
        configured) and 400 for malformed bodies. Duplicates are acknowledged
        with 200 so Telegram stops retrying them.
        """
        expected = os.environ.get("RIOCLARO_TELEGRAM_WEBHOOK_SECRET", "")
        received = request.httprequest.headers.get(SECRET_HEADER, "")
        if not expected or not hmac.compare_digest(
            received.encode(), expected.encode()
        ):
            _logger.warning("Telegram webhook rejected: invalid secret.")
            return Response("forbidden", status=403)

        body = request.httprequest.get_data(cache=False)
        if len(body) > MAX_BODY_BYTES:
            return Response("too large", status=400)
        try:
            update = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return Response("bad request", status=400)
        if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
            return Response("bad request", status=400)

        request.env["rioclaro.telegram.update"].sudo().enqueue(update)
        return Response("ok", status=200)
