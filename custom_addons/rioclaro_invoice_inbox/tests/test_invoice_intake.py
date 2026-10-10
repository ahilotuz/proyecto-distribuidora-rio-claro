from email.message import EmailMessage

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSupplierInvoiceEmailIntake(TransactionCase):
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
