"""Tests for creating intake records from Telegram messages."""

from unittest.mock import patch

from odoo.addons.rioclaro_invoice_inbox.models.assisted_query_ai import AssistedQueryAI
from odoo.addons.rioclaro_invoice_inbox.models.speech_client import (
    AzureSpeechClient,
    SpeechError,
)
from odoo.addons.rioclaro_invoice_inbox.models.telegram_client import (
    TelegramClient,
    TelegramError,
    TelegramFile,
    TelegramMessage,
)
from odoo.tests import TransactionCase, tagged



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


def _text_message(
    message_id: int = 50, sender_id: int = 777, text: str = "ventas recientes"
) -> TelegramMessage:
    """Build a private Telegram text message."""
    return TelegramMessage(
        update_id=2,
        message_id=message_id,
        chat_id=555,
        sender_id=sender_id,
        sender_name="proveedor",
        caption="",
        file=None,
        text=text,
    )


def _voice_message(
    message_id: int = 60, sender_id: int = 777, duration: int = 8
) -> TelegramMessage:
    """Build a private Telegram OGG/Opus voice message."""
    return TelegramMessage(
        update_id=3,
        message_id=message_id,
        chat_id=555,
        sender_id=sender_id,
        sender_name="proveedor",
        caption="",
        file=None,
        voice=TelegramFile(
            file_id="voice-1",
            file_name=f"voice-{message_id}.ogg",
            mimetype="audio/ogg",
            duration_seconds=duration,
        ),
    )


@tagged("post_install", "-at_install")
class TestTelegramIntake(TransactionCase):
    def setUp(self):
        super().setUp()
        # Polling tests must not depend on the developer's webhook configuration.
        webhook_env = patch.dict("os.environ", {"RIOCLARO_TELEGRAM_WEBHOOK_SECRET": ""})
        webhook_env.start()
        self.addCleanup(webhook_env.stop)
        self.intake = self.env["rioclaro.invoice.intake"]
        self.env["rioclaro.telegram.contact"].create(
            {"name": "Proveedor", "telegram_user_id": "777", "role": "internal"}
        )
        self.env["ir.config_parameter"].sudo().set_bool(
            "rioclaro_invoice_inbox.telegram_polling_enabled", True
        )

    def test_acl_allows_only_contacts_with_the_permission(self):
        self.assertTrue(self.intake._is_telegram_sender_allowed(777))
        self.assertFalse(self.intake._is_telegram_sender_allowed(999))

    def test_inactive_contact_is_denied(self):
        self.env["rioclaro.telegram.contact"].search(
            [("telegram_user_id", "=", "777")]
        ).active = False
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

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:fake"})
    @patch.object(TelegramClient, "get_updates")
    @patch.object(TelegramClient, "send_message")
    @patch.object(AssistedQueryAI, "answer")
    def test_private_text_query_is_answered_and_audited(
        self, answer_mock, send_mock, updates_mock
    ):
        updates_mock.return_value = (203, [_text_message()])
        answer_mock.return_value = {
            "ok": True,
            "tool": "recent_sales",
            "rows": [{"reference": "S0001"}],
            "answer": "Tus ventas recientes: S0001.",
        }

        self.assertEqual(self.intake.cron_poll_telegram(), 0)

        answer_mock.assert_called_once_with(777, "ventas recientes")
        send_mock.assert_called_once_with(555, "Tus ventas recientes: S0001.")
        audit = self.env["rioclaro.assisted.query.audit"].sudo().search(
            [("source_message_id", "=", "telegram:555:50")]
        )
        self.assertEqual(audit.telegram_user_id, "777")
        self.assertEqual(audit.tool, "recent_sales")
        self.assertEqual(audit.outcome, "allowed")
        self.assertNotIn("question", audit._fields)
        self.assertNotIn("response", audit._fields)

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:fake"})
    @patch.object(TelegramClient, "get_updates")
    @patch.object(TelegramClient, "send_message")
    @patch.object(AssistedQueryAI, "answer")
    def test_unknown_text_sender_is_denied_before_ai(
        self, answer_mock, send_mock, updates_mock
    ):
        updates_mock.return_value = (204, [_text_message(sender_id=999)])

        self.intake.cron_poll_telegram()

        answer_mock.assert_not_called()
        send_mock.assert_called_once_with(555, "No autorizado para consultas.")
        audit = self.env["rioclaro.assisted.query.audit"].sudo().search(
            [("source_message_id", "=", "telegram:555:50")]
        )
        self.assertEqual(audit.outcome, "denied")
        self.assertEqual(audit.telegram_user_id, "999")

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:fake"})
    @patch.object(TelegramClient, "get_updates")
    @patch.object(TelegramClient, "send_message")
    @patch.object(AssistedQueryAI, "answer")
    def test_query_rate_limit_is_five_per_contact_per_five_minutes(
        self, answer_mock, send_mock, updates_mock
    ):
        audit_model = self.env["rioclaro.assisted.query.audit"]
        contact = self.env["rioclaro.telegram.contact"].search(
            [("telegram_user_id", "=", "777")]
        )
        for index in range(5):
            audit_model.log_request(
                telegram_user_id=777,
                source_message_id=f"telegram:555:old-{index}",
                outcome="allowed",
                contact=contact,
                tool="recent_sales",
            )
        updates_mock.return_value = (205, [_text_message(message_id=51)])

        self.intake.cron_poll_telegram()

        answer_mock.assert_not_called()
        send_mock.assert_called_once_with(
            555, "Límite alcanzado: intenta de nuevo en unos minutos."
        )
        audit = audit_model.sudo().search(
            [("source_message_id", "=", "telegram:555:51")]
        )
        self.assertEqual(audit.outcome, "rate_limited")

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:fake"})
    @patch.object(TelegramClient, "get_updates")
    @patch.object(TelegramClient, "send_message")
    @patch.object(AssistedQueryAI, "answer", return_value={"ok": True, "answer": "ok", "tool": "recent_sales"})
    def test_query_rate_limit_is_configurable(
        self, answer_mock, send_mock, updates_mock
    ):
        self.env["ir.config_parameter"].sudo().set_int(
            "rioclaro_invoice_inbox.telegram_query_rate_limit", 6
        )
        audit_model = self.env["rioclaro.assisted.query.audit"]
        contact = self.env["rioclaro.telegram.contact"].search(
            [("telegram_user_id", "=", "777")]
        )
        for index in range(5):
            audit_model.log_request(
                telegram_user_id=777,
                source_message_id=f"telegram:555:cfg-{index}",
                outcome="allowed",
                contact=contact,
                tool="recent_sales",
            )
        updates_mock.return_value = (206, [_text_message(message_id=52)])

        self.intake.cron_poll_telegram()

        answer_mock.assert_called_once()

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:fake"})
    @patch.object(TelegramClient, "get_updates")
    @patch.object(TelegramClient, "send_message")
    @patch.object(AssistedQueryAI, "answer")
    def test_duplicate_text_message_is_not_run_twice(
        self, answer_mock, _send_mock, updates_mock
    ):
        updates_mock.return_value = (206, [_text_message(message_id=52)])
        answer_mock.return_value = {
            "ok": True,
            "tool": "recent_sales",
            "rows": [],
            "answer": "No hay resultados.",
        }

        self.intake.cron_poll_telegram()
        self.intake.cron_poll_telegram()

        answer_mock.assert_called_once()

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:fake"})
    @patch.dict(
        "os.environ",
        {
            "RIOCLARO_SPEECH_ENDPOINT": "https://canadacentral.api.cognitive.microsoft.com/",
            "RIOCLARO_SPEECH_REGION": "canadacentral",
            "RIOCLARO_SPEECH_KEY": "fake-speech-key",
        },
    )
    @patch.object(TelegramClient, "get_updates")
    @patch.object(TelegramClient, "download_file", return_value=b"ogg-audio")
    @patch.object(TelegramClient, "send_voice")
    @patch.object(TelegramClient, "send_message")
    @patch.object(AzureSpeechClient, "transcribe", return_value="¿Qué hay en inventario?")
    @patch.object(AzureSpeechClient, "synthesize", return_value=b"fake-mp3")
    @patch.object(AssistedQueryAI, "answer")
    def test_voice_query_is_transcribed_answered_spoken_and_audited(
        self,
        answer_mock,
        _synthesize_mock,
        _transcribe_mock,
        send_text_mock,
        send_voice_mock,
        download_mock,
        updates_mock,
    ):
        updates_mock.return_value = (207, [_voice_message()])
        answer_mock.return_value = {
            "ok": True,
            "tool": "stock_by_product",
            "rows": [{"product": "Escritorio", "on_hand": 3}],
            "answer": "Hay 3 escritorios disponibles.",
        }

        self.intake.cron_poll_telegram()

        download_mock.assert_called_once_with("voice-1")
        answer_mock.assert_called_once_with(777, "¿Qué hay en inventario?")
        send_voice_mock.assert_called_once_with(555, b"fake-mp3")
        send_text_mock.assert_not_called()
        audit = self.env["rioclaro.assisted.query.audit"].sudo().search(
            [("source_message_id", "=", "telegram:555:60")]
        )
        self.assertEqual(audit.input_type, "voice")
        self.assertEqual(audit.tool, "stock_by_product")
        self.assertEqual(audit.outcome, "allowed")

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:fake"})
    @patch.dict(
        "os.environ",
        {
            "RIOCLARO_SPEECH_ENDPOINT": "https://canadacentral.api.cognitive.microsoft.com/",
            "RIOCLARO_SPEECH_REGION": "canadacentral",
            "RIOCLARO_SPEECH_KEY": "fake-speech-key",
        },
    )
    @patch.object(TelegramClient, "get_updates")
    @patch.object(TelegramClient, "download_file", return_value=b"ogg-audio")
    @patch.object(TelegramClient, "send_message")
    @patch.object(AzureSpeechClient, "transcribe", side_effect=SpeechError("no match"))
    @patch.object(AssistedQueryAI, "answer")
    def test_voice_transcription_failure_is_audited_without_calling_ai(
        self, answer_mock, _transcribe_mock, send_mock, _download_mock, updates_mock
    ):
        updates_mock.return_value = (208, [_voice_message(message_id=61)])

        self.intake.cron_poll_telegram()

        answer_mock.assert_not_called()
        send_mock.assert_called_once()
        self.assertIn("No pude transcribir", send_mock.call_args.args[1])
        audit = self.env["rioclaro.assisted.query.audit"].sudo().search(
            [("source_message_id", "=", "telegram:555:61")]
        )
        self.assertEqual(audit.input_type, "voice")
        self.assertEqual(audit.outcome, "error")

    @patch.dict("os.environ", {"RIOCLARO_TELEGRAM_BOT_TOKEN": "123:fake"})
    @patch.object(TelegramClient, "get_updates")
    @patch.object(TelegramClient, "download_file")
    @patch.object(TelegramClient, "send_message")
    @patch.object(AssistedQueryAI, "answer")
    def test_voice_over_sixty_seconds_is_rejected_before_download(
        self, answer_mock, send_mock, download_mock, updates_mock
    ):
        updates_mock.return_value = (
            209,
            [_voice_message(message_id=62, duration=61)],
        )

        self.intake.cron_poll_telegram()

        download_mock.assert_not_called()
        answer_mock.assert_not_called()
        self.assertIn("60 segundos", send_mock.call_args.args[1])
        audit = self.env["rioclaro.assisted.query.audit"].sudo().search(
            [("source_message_id", "=", "telegram:555:62")]
        )
        self.assertEqual(audit.input_type, "voice")
        self.assertEqual(audit.outcome, "invalid")
