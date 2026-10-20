"""Tests for the Telegram Bot API client (all network calls are mocked)."""

import json
from unittest.mock import Mock, patch

from odoo.addons.rioclaro_invoice_inbox.models.telegram_client import (
    TelegramClient,
    TelegramError,
)
from odoo.tests import TransactionCase, tagged

_URLOPEN = "odoo.addons.rioclaro_invoice_inbox.models.telegram_client.urlopen"


def _response(body: bytes) -> Mock:
    """Build a mock urlopen response that works as a context manager."""
    response = Mock()
    response.read.return_value = body
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


def _json_response(result: object, ok: bool = True) -> Mock:
    return _response(json.dumps({"ok": ok, "result": result}).encode())


def _update(update_id: int, **message_fields: object) -> dict:
    message = {
        "message_id": update_id * 10,
        "chat": {"id": 555},
        "from": {"id": 777, "username": "proveedor"},
    }
    message.update(message_fields)
    return {"update_id": update_id, "message": message}


@tagged("post_install", "-at_install")
class TestTelegramClient(TransactionCase):
    def setUp(self):
        super().setUp()
        self.client = TelegramClient("123:fake-token")

    def test_empty_token_is_rejected(self):
        with self.assertRaises(ValueError):
            TelegramClient("  ")

    @patch(_URLOPEN)
    def test_get_updates_parses_documents_and_photos(self, urlopen_mock):
        urlopen_mock.return_value = _json_response(
            [
                _update(
                    10,
                    caption="Factura de octubre",
                    document={
                        "file_id": "doc-1",
                        "file_name": "factura.pdf",
                        "mime_type": "application/pdf",
                    },
                ),
                _update(11, photo=[{"file_id": "small"}, {"file_id": "large"}]),
                _update(12, text="hola"),
                _update(13, document={"file_id": "x", "mime_type": "text/plain"}),
            ]
        )

        next_offset, messages = self.client.get_updates(offset=10)

        self.assertEqual(next_offset, 14)
        self.assertEqual([m.update_id for m in messages], [10, 11])
        self.assertEqual(messages[0].file.file_name, "factura.pdf")
        self.assertEqual(messages[0].caption, "Factura de octubre")
        self.assertEqual(messages[0].chat_id, 555)
        self.assertEqual(messages[0].sender_id, 777)
        self.assertEqual(messages[1].file.file_id, "large")
        self.assertEqual(messages[1].file.mimetype, "image/jpeg")

    @patch(_URLOPEN)
    def test_get_updates_without_updates_keeps_offset_empty(self, urlopen_mock):
        urlopen_mock.return_value = _json_response([])

        self.assertEqual(self.client.get_updates(), (None, []))

    @patch(_URLOPEN)
    def test_api_error_is_reported_without_token(self, urlopen_mock):
        urlopen_mock.return_value = _json_response(None, ok=False)

        with self.assertRaises(TelegramError) as context:
            self.client.get_updates()
        self.assertNotIn("fake-token", str(context.exception))

    @patch(_URLOPEN)
    def test_download_file_fetches_content(self, urlopen_mock):
        urlopen_mock.side_effect = [
            _json_response({"file_path": "documents/file_1.pdf"}),
            _response(b"%PDF-1.4 content"),
        ]

        self.assertEqual(self.client.download_file("doc-1"), b"%PDF-1.4 content")

    @patch(_URLOPEN)
    def test_download_file_rejects_missing_path_and_empty_content(self, urlopen_mock):
        urlopen_mock.return_value = _json_response({})
        with self.assertRaises(TelegramError):
            self.client.download_file("doc-1")

        urlopen_mock.side_effect = [
            _json_response({"file_path": "documents/file_1.pdf"}),
            _response(b""),
        ]
        with self.assertRaises(TelegramError):
            self.client.download_file("doc-1")

    @patch(_URLOPEN)
    def test_send_message_posts_text(self, urlopen_mock):
        urlopen_mock.return_value = _json_response({"message_id": 1})

        self.client.send_message(555, "Recibido")

        request = urlopen_mock.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/sendMessage"))
        self.assertIn(b"chat_id=555", request.data)
