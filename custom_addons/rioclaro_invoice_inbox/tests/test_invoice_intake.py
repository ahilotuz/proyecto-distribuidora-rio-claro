from email.message import EmailMessage
import json
from unittest.mock import Mock, patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSupplierInvoiceEmailIntake(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_bool(
            "rioclaro_invoice_inbox.screening_enabled",
            False,
        )

    def test_screening_keeps_invoice_intake_route(self):
        routes = [
            ("rioclaro.invoice.intake", False, {}, self.env.uid, False),
            ("res.partner", False, {}, self.env.uid, False),
        ]

        filtered = self.env["mail.thread"]._filter_invoice_intake_routes(
            routes,
            {
                "route": "inspect_attachments",
                "label": "supplier_invoice",
                "confidence": 0.99,
            },
            "<invoice@example.test>",
        )

        self.assertEqual(filtered, routes)

    def test_screening_removes_non_invoice_intake_route(self):
        routes = [
            ("rioclaro.invoice.intake", False, {}, self.env.uid, False),
            ("res.partner", False, {}, self.env.uid, False),
        ]

        filtered = self.env["mail.thread"]._filter_invoice_intake_routes(
            routes,
            {"route": "ignore", "label": "not_invoice", "confidence": 1.0},
            "<newsletter@example.test>",
        )

        self.assertEqual(filtered, [routes[1]])

    @patch("odoo.addons.rioclaro_invoice_inbox.models.mail_thread.urlopen")
    def test_screening_sends_plain_text_body_to_function(self, urlopen_mock):
        config = self.env["ir.config_parameter"].sudo()
        config.set_str(
            "rioclaro_invoice_inbox.screening_url",
            "http://127.0.0.1:7071/api/screen-email",
        )
        response = Mock()
        response.read.return_value = json.dumps(
            {
                "route": "inspect_attachments",
                "label": "supplier_invoice",
                "confidence": 1.0,
            }
        ).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen_mock.return_value = response

        result = self.env["mail.thread"]._classify_incoming_intake_email(
            {
                "message_id": "<invoice@example.test>",
                "body": "<p>Factura de proveedor adjunta.</p>",
                "attachments": [("invoice.pdf", b"%PDF-test")],
            },
            config,
        )

        request = urlopen_mock.call_args.args[0]
        self.assertEqual(
            json.loads(request.data),
            {
                "body": "Factura de proveedor adjunta.",
                "hasAttachments": True,
                "language": "es",
            },
        )
        self.assertEqual(result["label"], "supplier_invoice")

    def test_incoming_non_invoice_is_not_created_when_screening_is_enabled(self):
        self.env["ir.config_parameter"].sudo().set_bool(
            "rioclaro_invoice_inbox.screening_enabled",
            True,
        )
        self.env["ir.config_parameter"].sudo().set_str(
            "rioclaro_invoice_inbox.screening_url",
            "http://screening.example.test/api/screen-email",
        )
        email = EmailMessage()
        email["From"] = "news@example.test"
        email["To"] = "supplier-invoices@example.test"
        email["Subject"] = "Shipping update"
        email["Message-ID"] = "<shipping-test@example.test>"
        email.set_content("Your order has shipped.")
        response = Mock()
        response.read.return_value = json.dumps(
            {"route": "ignore", "label": "not_invoice", "confidence": 1.0}
        ).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with patch(
            "odoo.addons.rioclaro_invoice_inbox.models.mail_thread.urlopen",
            return_value=response,
        ):
            self.env["mail.thread"].sudo().message_process(
                "rioclaro.invoice.intake",
                email.as_string(),
            )

        self.assertFalse(
            self.env["rioclaro.invoice.intake"].search(
                [("source_message_id", "=", "<shipping-test@example.test>")]
            )
        )

    def test_incoming_classified_invoice_is_created_when_screening_is_enabled(self):
        self.env["ir.config_parameter"].sudo().set_bool(
            "rioclaro_invoice_inbox.screening_enabled",
            True,
        )
        self.env["ir.config_parameter"].sudo().set_str(
            "rioclaro_invoice_inbox.screening_url",
            "http://screening.example.test/api/screen-email",
        )
        email = EmailMessage()
        email["From"] = "billing@example.test"
        email["To"] = "supplier-invoices@example.test"
        email["Subject"] = "Supplier invoice test"
        email["Message-ID"] = "<invoice-screening-test@example.test>"
        email.set_content("The supplier invoice is attached.")
        email.add_attachment(
            b"%PDF-1.4\nsynthetic invoice",
            maintype="application",
            subtype="pdf",
            filename="synthetic-invoice.pdf",
        )
        response = Mock()
        response.read.return_value = json.dumps(
            {
                "route": "inspect_attachments",
                "label": "supplier_invoice",
                "confidence": 1.0,
            }
        ).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with patch(
            "odoo.addons.rioclaro_invoice_inbox.models.mail_thread.urlopen",
            return_value=response,
        ):
            self.env["mail.thread"].sudo().message_process(
                "rioclaro.invoice.intake",
                email.as_string(),
            )

        record = self.env["rioclaro.invoice.intake"].search(
            [("source_message_id", "=", "<invoice-screening-test@example.test>")],
            limit=1,
        )
        self.assertTrue(record)
        self.assertIn(
            "synthetic-invoice.pdf",
            record.message_ids.attachment_ids.mapped("name"),
        )

    def test_message_new_is_idempotent(self):
        model = self.env["rioclaro.invoice.intake"].sudo()
        msg_dict = {
            "message_id": "<invoice-001@example.test>",
            "email_from": "billing@supplier.example",
            "subject": "Invoice 001",
            "date": "2026-10-09 12:00:00",
        }

        first_id = model.message_new(msg_dict)
        duplicate_id = model.message_new(msg_dict)

        self.assertEqual(first_id, duplicate_id)
        self.assertEqual(
            model.search_count([("source_message_id", "=", msg_dict["message_id"])]),
            1,
        )
        self.assertEqual(first_id.state, "received")
        self.assertEqual(first_id.sender_email, msg_dict["email_from"])

    def test_message_new_uses_configured_company(self):
        company = self.env["res.company"].create(
            {"name": "Invoice Intake Test Company"}
        )
        self.env["ir.config_parameter"].sudo().set_int(
            "rioclaro_invoice_inbox.company_id",
            company.id,
        )

        record = self.env["rioclaro.invoice.intake"].sudo().message_new(
            {
                "message_id": "<configured-company@example.test>",
                "email_from": "billing@supplier.example",
                "subject": "Invoice company routing",
            }
        )

        self.assertEqual(record.company_id, company)

    def test_missing_message_id_uses_stable_fallback_key(self):
        model = self.env["rioclaro.invoice.intake"].sudo()
        msg_dict = {
            "message_id": " ",
            "email_from": "billing@supplier.example",
            "subject": "Invoice without message id",
            "date": "2026-10-09 12:00:00",
            "attachments": [("invoice.pdf", b"%PDF-1.4 invoice")],
        }

        first = model.message_new(msg_dict)
        duplicate = model.message_new(msg_dict)

        self.assertEqual(first, duplicate)
        self.assertTrue(first.source_message_id.startswith("generated:"))

    def test_incoming_email_creates_record_and_keeps_attachment(self):
        email = EmailMessage()
        email["From"] = "billing@supplier.example"
        email["To"] = "supplier-invoices@example.test"
        email["Subject"] = "Supplier invoice 002"
        email["Message-ID"] = "<invoice-002@example.test>"
        email.set_content("Please find the supplier invoice attached.")
        email.add_attachment(
            b"%PDF-1.4\ninvoice",
            maintype="application",
            subtype="pdf",
            filename="invoice-002.pdf",
        )

        self.env["mail.thread"].sudo().message_process(
            "rioclaro.invoice.intake",
            email.as_string(),
        )

        record = self.env["rioclaro.invoice.intake"].search(
            [("source_message_id", "=", "<invoice-002@example.test>")],
            limit=1,
        )
        self.assertTrue(record)
        self.assertEqual(record.name, "Supplier invoice 002")
        message = self.env["mail.message"].search(
            [
                ("model", "=", record._name),
                ("res_id", "=", record.id),
                ("message_id", "=", "<invoice-002@example.test>"),
            ],
            limit=1,
        )
        self.assertTrue(message)
        self.assertIn("invoice-002.pdf", message.attachment_ids.mapped("name"))

    def test_incoming_emails_from_multiple_mailboxes_share_the_inbox(self):
        intake_model = self.env["rioclaro.invoice.intake"]
        server_model = self.env["fetchmail.server"].sudo()
        target_model = self.env["ir.model"]._get("rioclaro.invoice.intake")
        mailboxes = (
            "accounts@company.example",
            "purchasing@company.example",
        )

        for index, mailbox in enumerate(mailboxes, start=1):
            server = server_model.create(
                {
                    "name": mailbox,
                    "server": "imap.company.example",
                    "server_type": "imap",
                    "port": 993,
                    "user": mailbox,
                    "object_id": target_model.id,
                }
            )
            email = EmailMessage()
            email["From"] = f"billing{index}@supplier.example"
            email["To"] = mailbox
            email["Subject"] = f"Supplier invoice {index}"
            email["Message-ID"] = f"<multi-inbox-{index}@example.test>"
            email.set_content("Please find the invoice attached.")

            self.env["mail.thread"].sudo().with_context(
                default_fetchmail_server_id=server.id
            ).message_process(
                intake_model._name,
                email.as_string(),
            )

        records = intake_model.search(
            [
                (
                    "source_message_id",
                    "in",
                    [
                        "<multi-inbox-1@example.test>",
                        "<multi-inbox-2@example.test>",
                    ],
                )
            ],
            order="source_message_id",
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(
            records.mapped("source_mailbox"),
            list(mailboxes),
        )

    def test_review_actions_update_state(self):
        record = self.env["rioclaro.invoice.intake"].sudo().create(
            {
                "name": "Invoice for review",
                "source_message_id": "<invoice-003@example.test>",
                "received_at": "2026-10-09 12:00:00",
            }
        )

        record.action_mark_in_review()
        self.assertEqual(record.state, "in_review")

        record.action_reject()
        self.assertEqual(record.state, "rejected")
