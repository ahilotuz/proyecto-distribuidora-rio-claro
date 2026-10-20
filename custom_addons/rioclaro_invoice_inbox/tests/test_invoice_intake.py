from email.message import EmailMessage
import json
from unittest.mock import Mock, patch

from odoo.exceptions import UserError
from odoo.addons.rioclaro_invoice_inbox.models.document_intelligence import (
    AzureDocumentIntelligenceClient,
    DocumentIntelligenceError,
)
from odoo.addons.rioclaro_invoice_inbox.models.document_intelligence import (
    ExtractedInvoiceLine,
    InvoiceExtraction,
)
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSupplierInvoiceEmailIntake(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_bool(
            "rioclaro_invoice_inbox.screening_enabled",
            False,
        )

    @patch(
        "odoo.addons.rioclaro_invoice_inbox.models.document_intelligence.urlopen"
    )
    def test_document_intelligence_extracts_invoice_fields_and_lines(
        self,
        urlopen_mock,
    ):
        endpoint = "https://resource.example.test"
        operation_url = (
            "https://resource.example.test/documentintelligence/"
            "documentModels/prebuilt-invoice/analyzeResults/operation-id"
        )
        accepted_response = Mock()
        accepted_response.headers = {"operation-location": operation_url}
        accepted_response.__enter__ = Mock(return_value=accepted_response)
        accepted_response.__exit__ = Mock(return_value=False)

        result_response = Mock()
        result_response.read.return_value = json.dumps(
            {
                "status": "succeeded",
                "analyzeResult": {
                    "documents": [
                        {
                            "fields": {
                                "VendorTaxId": {
                                    "valueString": "SUP990101AB1",
                                    "confidence": 0.98,
                                },
                                "VendorName": {
                                    "valueString": "Contoso Ltd.",
                                    "confidence": 0.98,
                                },
                                "InvoiceId": {
                                    "valueString": "FAC-1001",
                                    "confidence": 0.99,
                                },
                                "InvoiceDate": {
                                    "valueDate": "2026-10-15",
                                    "confidence": 0.97,
                                },
                                "SubTotal": {
                                    "valueCurrency": {"amount": 1000.0},
                                    "confidence": 0.95,
                                },
                                "TotalTax": {
                                    "valueCurrency": {"amount": 160.0},
                                    "confidence": 0.94,
                                },
                                "InvoiceTotal": {
                                    "valueCurrency": {
                                        "amount": 1160.0,
                                        "currencyCode": "MXN",
                                    },
                                    "confidence": 0.99,
                                },
                                "Items": {
                                    "valueArray": [
                                        {
                                            "confidence": 0.93,
                                            "valueObject": {
                                                "Description": {
                                                    "valueString": "Office supplies"
                                                },
                                                "Quantity": {"valueNumber": 2},
                                                "UnitPrice": {
                                                    "valueCurrency": {"amount": 500.0}
                                                },
                                                "Amount": {
                                                    "valueCurrency": {"amount": 1000.0}
                                                },
                                            },
                                        }
                                    ]
                                },
                            }
                        }
                    ]
                },
            }
        ).encode()
        result_response.__enter__ = Mock(return_value=result_response)
        result_response.__exit__ = Mock(return_value=False)
        urlopen_mock.side_effect = [accepted_response, result_response]

        extraction = AzureDocumentIntelligenceClient(
            endpoint,
            "test-key",
            poll_interval=0,
        ).analyze_invoice(b"%PDF-invoice", "application/pdf")

        self.assertEqual(extraction.supplier_rfc, "SUP990101AB1")
        self.assertEqual(extraction.supplier_name, "Contoso Ltd.")
        self.assertEqual(extraction.invoice_number, "FAC-1001")
        self.assertEqual(extraction.invoice_date, "2026-10-15")
        self.assertEqual(extraction.currency_code, "MXN")
        self.assertEqual(extraction.amount_untaxed, 1000.0)
        self.assertEqual(extraction.amount_tax, 160.0)
        self.assertEqual(extraction.amount_total, 1160.0)
        self.assertEqual(extraction.confidence_scores["amount_total"], 0.99)
        self.assertEqual(extraction.lines[0].description, "Office supplies")
        self.assertEqual(extraction.lines[0].quantity, 2.0)
        self.assertEqual(extraction.lines[0].unit_price, 500.0)
        self.assertEqual(extraction.lines[0].amount, 1000.0)
        self.assertEqual(urlopen_mock.call_count, 2)
        self.assertEqual(
            urlopen_mock.call_args_list[0].args[0].data,
            b"%PDF-invoice",
        )

    def test_document_intelligence_rejects_unsupported_documents(self):
        client = AzureDocumentIntelligenceClient(
            "https://resource.example.test",
            "test-key",
        )

        with self.assertRaises(ValueError):
            client.analyze_invoice(b"not a supported document", "text/plain")

    @patch(
        "odoo.addons.rioclaro_invoice_inbox.models.document_intelligence.urlopen"
    )
    def test_document_intelligence_rejects_untrusted_operation_url(
        self,
        urlopen_mock,
    ):
        response = Mock()
        response.headers = {
            "operation-location": "https://attacker.example.test/operation"
        }
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen_mock.return_value = response
        client = AzureDocumentIntelligenceClient(
            "https://resource.example.test",
            "test-key",
        )

        with self.assertRaises(DocumentIntelligenceError):
            client.analyze_invoice(b"%PDF-invoice", "application/pdf")

        self.assertEqual(urlopen_mock.call_count, 1)

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

    def test_action_create_vendor_bill_success(self):
        partner = self.env["res.partner"].create({"name": "Test Supplier SA", "vat": "SUP990101AB1"})
        record = self.env["rioclaro.invoice.intake"].sudo().create(
            {
                "name": "Supplier Invoice 101",
                "source_message_id": "<invoice-bill-test-1@example.test>",
                "partner_id": partner.id,
                "supplier_rfc": "SUP990101AB1",
                "invoice_number": "FAC-1001",
                "invoice_date": "2026-10-15",
                "amount_untaxed": 1000.0,
                "amount_tax": 160.0,
                "amount_total": 1160.0,
            }
        )
        self.env["ir.attachment"].create(
            {
                "name": "test_invoice.pdf",
                "res_model": "rioclaro.invoice.intake",
                "res_id": record.id,
                "raw": b"%PDF-1.4 test invoice raw content",
            }
        )

        action = record.action_create_vendor_bill()

        self.assertEqual(record.state, "invoiced")
        self.assertTrue(record.invoice_id)
        self.assertEqual(record.invoice_id.move_type, "in_invoice")
        self.assertEqual(record.invoice_id.state, "draft")
        self.assertEqual(record.invoice_id.partner_id, partner)
        self.assertEqual(record.invoice_id.ref, "FAC-1001")
        self.assertEqual(action["res_model"], "account.move")
        self.assertEqual(action["res_id"], record.invoice_id.id)

        bill_attachments = self.env["ir.attachment"].search(
            [("res_model", "=", "account.move"), ("res_id", "=", record.invoice_id.id)]
        )
        self.assertIn("test_invoice.pdf", bill_attachments.mapped("name"))

    def test_action_create_vendor_bill_auto_creates_partner_from_rfc(self):
        record = self.env["rioclaro.invoice.intake"].sudo().create(
            {
                "name": "Supplier Invoice From RFC",
                "source_message_id": "<invoice-bill-test-2@example.test>",
                "supplier_rfc": "NEW990101XYZ",
                "amount_total": 500.0,
            }
        )

        record.action_create_vendor_bill()

        self.assertTrue(record.partner_id)
        self.assertEqual(record.partner_id.vat, "NEW990101XYZ")
        self.assertEqual(record.invoice_id.partner_id, record.partner_id)

    def test_action_create_vendor_bill_fails_without_vendor_or_rfc(self):
        record = self.env["rioclaro.invoice.intake"].sudo().create(
            {
                "name": "Incomplete Invoice Intake",
                "source_message_id": "<invoice-bill-test-3@example.test>",
            }
        )

        with self.assertRaises(UserError):
            record.action_create_vendor_bill()

    def test_action_create_vendor_bill_fails_if_already_invoiced(self):
        partner = self.env["res.partner"].create({"name": "Existing Supplier", "vat": "EXT880101AB1"})
        record = self.env["rioclaro.invoice.intake"].sudo().create(
            {
                "name": "Invoice To Be Invoiced Twice",
                "source_message_id": "<invoice-bill-test-4@example.test>",
                "partner_id": partner.id,
            }
        )
        record.action_create_vendor_bill()

        with self.assertRaises(UserError):
            record.action_create_vendor_bill()

    def test_action_create_vendor_bill_fails_if_rejected(self):
        partner = self.env["res.partner"].create({"name": "Rejected Supplier", "vat": "REJ770101AB1"})
        record = self.env["rioclaro.invoice.intake"].sudo().create(
            {
                "name": "Rejected Invoice Intake",
                "source_message_id": "<invoice-bill-test-5@example.test>",
                "partner_id": partner.id,
                "state": "rejected",
            }
        )

        with self.assertRaises(UserError):
            record.action_create_vendor_bill()

    def test_onchange_supplier_rfc_matches_existing_partner(self):
        partner = self.env["res.partner"].create({"name": "RFC Match Supplier", "vat": "RFC110203ABC"})
        record = self.env["rioclaro.invoice.intake"].new(
            {
                "name": "Testing RFC matching",
                "supplier_rfc": "rfc110203abc",
                "company_id": self.env.company.id,
            }
        )
        record._onchange_supplier_rfc()
        self.assertEqual(record.partner_id, partner)

    def _create_intake_with_pdf(self, message_id: str):
        """Create an intake record with a PDF attachment for extraction tests."""
        record = self.env["rioclaro.invoice.intake"].sudo().create(
            {"name": "Extraction test", "source_message_id": message_id}
        )
        self.env["ir.attachment"].create(
            {
                "name": "invoice.pdf",
                "mimetype": "application/pdf",
                "raw": b"%PDF-1.4 test",
                "res_model": "rioclaro.invoice.intake",
                "res_id": record.id,
            }
        )
        return record

    def _fake_extraction(self) -> InvoiceExtraction:
        """Return a canned Azure extraction result."""
        return InvoiceExtraction(
            supplier_rfc="SUP990101AB1",
            supplier_name="Test Supplier SA",
            invoice_number="INV-100",
            invoice_date="2026-10-15",
            currency_code="USD",
            amount_untaxed=100.0,
            amount_tax=10.0,
            amount_total=110.0,
            confidence_scores={},
            lines=(
                ExtractedInvoiceLine(
                    description="Widget",
                    quantity=2.0,
                    unit_price=50.0,
                    amount=100.0,
                    confidence=0.9,
                ),
            ),
        )

    @patch.dict(
        "os.environ",
        {
            "RIOCLARO_DOCUMENT_INTELLIGENCE_ENDPOINT": "https://example.test",
            "RIOCLARO_DOCUMENT_INTELLIGENCE_KEY": "fake-key",
        },
    )
    @patch.object(AzureDocumentIntelligenceClient, "analyze_invoice")
    def test_action_extract_invoice_data_success(self, analyze_mock):
        analyze_mock.return_value = self._fake_extraction()
        record = self._create_intake_with_pdf("<extract-ok@example.test>")

        self.assertTrue(record.action_extract_invoice_data())

        self.assertEqual(record.extraction_state, "extracted")
        self.assertEqual(record.invoice_number, "INV-100")
        self.assertEqual(record.supplier_rfc, "SUP990101AB1")
        self.assertEqual(record.amount_total, 110.0)
        self.assertEqual(record.currency_id.name, "USD")
        self.assertEqual(len(record.extracted_line_ids), 1)
        self.assertEqual(record.extracted_line_ids.description, "Widget")

    @patch.dict(
        "os.environ",
        {
            "RIOCLARO_DOCUMENT_INTELLIGENCE_ENDPOINT": "https://example.test",
            "RIOCLARO_DOCUMENT_INTELLIGENCE_KEY": "fake-key",
        },
    )
    @patch.object(
        AzureDocumentIntelligenceClient,
        "analyze_invoice",
        side_effect=DocumentIntelligenceError("boom"),
    )
    def test_action_extract_invoice_data_azure_error_keeps_data(self, _analyze_mock):
        record = self._create_intake_with_pdf("<extract-error@example.test>")

        self.assertFalse(record.action_extract_invoice_data())

        self.assertEqual(record.extraction_state, "error")
        self.assertFalse(record.extracted_line_ids)
        self.assertTrue(
            self.env["ir.attachment"].search_count(
                [("res_model", "=", "rioclaro.invoice.intake"), ("res_id", "=", record.id)]
            )
        )

    @patch.dict(
        "os.environ",
        {
            "RIOCLARO_DOCUMENT_INTELLIGENCE_ENDPOINT": "https://example.test",
            "RIOCLARO_DOCUMENT_INTELLIGENCE_KEY": "fake-key",
        },
    )
    def test_action_extract_invoice_data_requires_attachment(self):
        record = self.env["rioclaro.invoice.intake"].sudo().create(
            {"name": "No attachment", "source_message_id": "<extract-none@example.test>"}
        )
        with self.assertRaises(UserError):
            record.action_extract_invoice_data()

    @patch.dict(
        "os.environ",
        {
            "RIOCLARO_DOCUMENT_INTELLIGENCE_ENDPOINT": "",
            "RIOCLARO_DOCUMENT_INTELLIGENCE_KEY": "",
        },
    )
    def test_action_extract_invoice_data_requires_configuration(self):
        record = self._create_intake_with_pdf("<extract-config@example.test>")
        with self.assertRaises(UserError):
            record.action_extract_invoice_data()

    def test_action_create_vendor_bill_uses_extracted_lines(self):
        partner = self.env["res.partner"].create({"name": "Lines Supplier SA"})
        record = self.env["rioclaro.invoice.intake"].sudo().create(
            {
                "name": "Lines invoice",
                "source_message_id": "<bill-lines@example.test>",
                "partner_id": partner.id,
                "amount_untaxed": 150.0,
                "extracted_line_ids": [
                    (0, 0, {"description": "Widget", "quantity": 2.0, "unit_price": 50.0, "amount": 100.0}),
                    (0, 0, {"description": "Service", "quantity": 1.0, "amount": 50.0}),
                ],
            }
        )

        record.action_create_vendor_bill()

        lines = record.invoice_id.invoice_line_ids
        self.assertEqual(len(lines), 2)
        self.assertEqual(sorted(lines.mapped("name")), ["Service", "Widget"])
        self.assertEqual(record.invoice_id.amount_untaxed, 150.0)
