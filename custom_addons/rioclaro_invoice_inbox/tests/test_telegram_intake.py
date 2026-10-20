"""Tests for creating intake records from Telegram messages."""

from unittest.mock import patch

from odoo.addons.rioclaro_invoice_inbox.models.telegram_client import (
    TelegramClient,
    TelegramError,
    TelegramFile,
    TelegramMessage,
)
from odoo.tests import TransactionCase, tagged

_ALLOWED_PARAM = "rioclaro_invoice_inbox.telegram_allowed_user_ids"


def _message(message_id: int = 5, sender_id: int = 777) -> TelegramMessage:
    """Build a Telegram message carrying a PDF file."""
    return TelegramMessage(
        update_id=1,
        message_id=message_id,
        chat_id=555,
        sender_id=sender_id,
        sender_name="proveedor",
        caption="Factura de octubre",
        file=TelegramFile(
            file_id="doc-1", file_name="factura.pdf", mimetype="application/pdf"
        ),
    )


@tagged("post_install", "-at_install")
class TestTelegramIntake(TransactionCase):
    def setUp(self):
        super().setUp()
        self.intake = self.env["rioclaro.invoice.intake"]
        self.env["ir.config_parameter"].sudo().set_str(_ALLOWED_PARAM, "111, 777")
        self.env["ir.config_parameter"].sudo().set_bool(
            "rioclaro_invoice_inbox.telegram_polling_enabled", True
        )

    def test_allowlist_matches_only_configured_ids(self):
        self.assertTrue(self.intake._is_telegram_sender_allowed(777))
        self.assertFalse(self.intake._is_telegram_sender_allowed(999))

    def test_empty_allowlist_allows_nobody(self):
        self.env["ir.config_parameter"].sudo().set_str(_ALLOWED_PARAM, "")
        self.assertFalse(self.intake._is_telegram_sender_allowed(777))

    def test_disallowed_sender_creates_nothing(self):
        before = self.intake.search_count([])

        record = self.intake.create_from_telegram(_message(sender_id=999), b"%PDF")

        self.assertFalse(record)
        self.assertEqual(self.intake.search_count([]), before)

    def test_allowed_message_creates_record_with_attachment(self):
        record = self.intake.create_from_telegram(_message(), b"%PDF-1.4 data")

        self.assertEqual(record.source_message_id, "telegram:555:5")
        self.assertEqual(record.name, "Factura de octubre")
        self.assertEqual(record.source_mailbox, "Telegram")
        self.assertEqual(record.extraction_state, "pending")
        attachment = record._get_extractable_attachment()
        self.assertEqual(attachment.name, "factura.pdf")
        self.assertEqual(bytes(attachment.raw), b"%PDF-1.4 data")

    def test_repeated_message_is_not_duplicated(self):
        first = self.intake.create_from_telegram(_message(), b"%PDF-1.4 data")
        second = self.intake.create_from_telegram(_message(), b"%PDF-1.4 data")

        self.assertEqual(first, second)
        self.assertEqual(
            self.env["ir.attachment"].search_count(
                [("res_model", "=", "rioclaro.invoice.intake"), ("res_id", "=", first.id)]
            ),
            1,
        )

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:fake"})
    @patch.object(TelegramClient, "get_updates")
    def test_polling_is_skipped_when_not_enabled_in_database(self, get_updates_mock):
        self.env["ir.config_parameter"].sudo().set_bool(
            "rioclaro_invoice_inbox.telegram_polling_enabled", False
        )

        self.assertEqual(self.intake.cron_poll_telegram(), 0)

        get_updates_mock.assert_not_called()

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": ""})
    def test_polling_is_skipped_without_token(self):
        self.assertEqual(self.intake.cron_poll_telegram(), 0)

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:fake"})
    @patch.object(TelegramClient, "send_message")
    @patch.object(TelegramClient, "download_file", return_value=b"%PDF-1.4 data")
    @patch.object(TelegramClient, "get_updates")
    def test_polling_creates_records_replies_and_saves_offset(
        self, get_updates_mock, _download_mock, send_mock
    ):
        get_updates_mock.return_value = (
            101,
            [_message(message_id=8), _message(message_id=9, sender_id=999)],
        )

        created = self.intake.cron_poll_telegram()

        self.assertEqual(created, 1)
        self.assertTrue(self.intake.search([("source_message_id", "=", "telegram:555:8")]))
        self.assertFalse(self.intake.search([("source_message_id", "=", "telegram:555:9")]))
        replies = [call.args[1] for call in send_mock.call_args_list]
        self.assertTrue(replies[0].startswith("Recibido"))
        self.assertEqual(replies[1], "No autorizado.")
        self.assertEqual(
            self.env["ir.config_parameter"]
            .sudo()
            .get_int("rioclaro_invoice_inbox.telegram_update_offset"),
            101,
        )

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:fake"})
    @patch.object(TelegramClient, "send_message")
    @patch.object(
        TelegramClient, "download_file", side_effect=TelegramError("download failed")
    )
    @patch.object(TelegramClient, "get_updates")
    def test_polling_keeps_offset_when_a_download_fails(
        self, get_updates_mock, _download_mock, _send_mock
    ):
        get_updates_mock.return_value = (101, [_message(message_id=8)])
        self.env["ir.config_parameter"].sudo().set_int(
            "rioclaro_invoice_inbox.telegram_update_offset", 50
        )

        self.assertEqual(self.intake.cron_poll_telegram(), 0)

        self.assertEqual(
            self.env["ir.config_parameter"]
            .sudo()
            .get_int("rioclaro_invoice_inbox.telegram_update_offset"),
            50,
        )
        self.assertFalse(self.intake.search([("source_message_id", "=", "telegram:555:8")]))

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:fake"})
    @patch.object(TelegramClient, "get_updates", side_effect=TelegramError("down"))
    def test_polling_survives_a_telegram_outage(self, _get_updates_mock):
        self.assertEqual(self.intake.cron_poll_telegram(), 0)
