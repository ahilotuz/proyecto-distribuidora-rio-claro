"""Tests for the Telegram webhook endpoint and its processing queue."""

import json
from unittest.mock import MagicMock, patch

from odoo.addons.rioclaro_invoice_inbox.models.telegram_client import (
    TelegramClient,
    TelegramError,
)
from odoo.tests import HttpCase, TransactionCase, tagged

SECRET = "s3cret-token"
URL = "/rioclaro/telegram/webhook"
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": SECRET, "Content-Type": "application/json"}


def _update(update_id: int = 1001, chat_type: str = "private", text: str = "hola") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 7,
            "chat": {"id": 55, "type": chat_type},
            "from": {"id": 55, "first_name": "Ana"},
            "text": text,
        },
    }


@tagged("post_install", "-at_install")
class TestTelegramWebhookEndpoint(HttpCase):
    """The public endpoint must authenticate, validate and deduplicate."""

    def setUp(self) -> None:
        super().setUp()
        env_patch = patch.dict(
            "os.environ", {"RIOCLARO_TELEGRAM_WEBHOOK_SECRET": SECRET}
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def _post(self, body, headers=None):
        data = body if isinstance(body, (str, bytes)) else json.dumps(body)
        return self.url_open(URL, data=data, headers=headers or HEADERS)

    def _count(self) -> int:
        return self.env["rioclaro.telegram.update"].sudo().search_count([])

    def test_valid_update_is_queued_and_acknowledged(self):
        before = self._count()
        response = self._post(_update(2001))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._count(), before + 1)

    def test_wrong_secret_is_forbidden_and_not_queued(self):
        before = self._count()
        response = self._post(
            _update(2002),
            {"X-Telegram-Bot-Api-Secret-Token": "forged", "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._count(), before)

    def test_missing_secret_header_is_forbidden(self):
        response = self._post(_update(2003), {"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 403)

    def test_webhook_mode_not_configured_rejects_everything(self):
        with patch.dict("os.environ", {"RIOCLARO_TELEGRAM_WEBHOOK_SECRET": ""}):
            response = self._post(_update(2004), {"X-Telegram-Bot-Api-Secret-Token": ""})
        self.assertEqual(response.status_code, 403)

    def test_duplicate_delivery_is_acknowledged_but_stored_once(self):
        self._post(_update(2005))
        before = self._count()
        response = self._post(_update(2005))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._count(), before)

    def test_malformed_bodies_are_rejected(self):
        self.assertEqual(self._post("not json").status_code, 400)
        self.assertEqual(self._post({"no_update_id": 1}).status_code, 400)
        self.assertEqual(self._post([1, 2]).status_code, 400)

    def test_oversized_body_is_rejected(self):
        response = self._post(_update(2006, text="x" * (300 * 1024)))
        self.assertEqual(response.status_code, 400)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.url_open(URL).status_code, 405)


@tagged("post_install", "-at_install")
class TestTelegramUpdateQueue(TransactionCase):
    """Queue processing: success, retry, give-up and privacy."""

    def setUp(self) -> None:
        super().setUp()
        env_patch = patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:abc"})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.queue = self.env["rioclaro.telegram.update"]

    def test_enqueue_returns_false_for_duplicates(self):
        self.assertTrue(self.queue.enqueue(_update(3001)))
        self.assertFalse(self.queue.enqueue(_update(3001)))
        self.assertEqual(self.queue.search_count([("update_id", "=", 3001)]), 1)

    def test_processing_marks_done_and_erases_payload(self):
        self.queue.enqueue(_update(3002))
        with patch.object(
            type(self.env["rioclaro.invoice.intake"]), "_handle_telegram_message"
        ) as handler:
            self.assertEqual(self.queue.cron_process_pending(), 1)
        handler.assert_called_once()
        record = self.queue.search([("update_id", "=", 3002)])
        self.assertEqual(record.state, "done")
        self.assertFalse(record.payload)

    def test_group_chat_updates_are_finished_without_handling(self):
        self.queue.enqueue(_update(3003, chat_type="group"))
        with patch.object(
            type(self.env["rioclaro.invoice.intake"]), "_handle_telegram_message"
        ) as handler:
            self.queue.cron_process_pending()
        handler.assert_not_called()
        self.assertEqual(self.queue.search([("update_id", "=", 3003)]).state, "done")

    def test_telegram_failure_is_retried_then_marked_failed(self):
        self.queue.enqueue(_update(3004))
        record = self.queue.search([("update_id", "=", 3004)])
        with patch.object(
            type(self.env["rioclaro.invoice.intake"]),
            "_handle_telegram_message",
            side_effect=TelegramError("down"),
        ):
            self.queue.cron_process_pending()
            self.assertEqual((record.state, record.attempts), ("pending", 1))
            for _ in range(4):
                self.queue.cron_process_pending()
        self.assertEqual((record.state, record.attempts), ("failed", 5))
        self.assertFalse(record.payload)

    def test_processing_is_skipped_without_bot_token(self):
        self.queue.enqueue(_update(3005))
        with patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": ""}):
            self.assertEqual(self.queue.cron_process_pending(), 0)
        self.assertEqual(self.queue.search([("update_id", "=", 3005)]).state, "pending")

    def test_polling_is_skipped_in_webhook_mode(self):
        with patch.dict("os.environ", {"RIOCLARO_TELEGRAM_WEBHOOK_SECRET": SECRET}), patch(
            "odoo.addons.rioclaro_invoice_inbox.models.invoice_intake.TelegramClient"
        ) as client:
            self.assertEqual(self.env["rioclaro.invoice.intake"].cron_poll_telegram(), 0)
        client.assert_not_called()


class TestTelegramClientWebhook(TransactionCase):
    """Client helpers used to register the webhook."""

    def test_set_webhook_requires_https_and_valid_secret(self):
        client = TelegramClient("123:abc")
        with self.assertRaises(ValueError):
            client.set_webhook("http://x.example/hook", SECRET)
        with self.assertRaises(ValueError):
            client.set_webhook("https://x.example/hook", "")

    def test_set_webhook_sends_secret_and_message_updates_only(self):
        client = TelegramClient("123:abc")
        client._call = MagicMock(return_value=True)
        client.set_webhook("https://x.example/hook", SECRET)
        method, params = client._call.call_args.args
        self.assertEqual(method, "setWebhook")
        self.assertEqual(params["secret_token"], SECRET)
        self.assertEqual(json.loads(params["allowed_updates"]), ["message"])

    def test_parse_update_ignores_invalid_input(self):
        self.assertIsNone(TelegramClient.parse_update({"message": {}}))
        self.assertIsNone(TelegramClient.parse_update(_update(1, chat_type="supergroup")))
        self.assertEqual(TelegramClient.parse_update(_update(9)).update_id, 9)
