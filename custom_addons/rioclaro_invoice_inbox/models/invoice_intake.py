import json
import logging
import os
from hashlib import sha256

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .document_intelligence import (
    AzureDocumentIntelligenceClient,
    DocumentIntelligenceError,
)
from .telegram_client import TelegramClient, TelegramError, TelegramMessage

_logger = logging.getLogger(__name__)


_INTAKE_COMPANY_PARAM = "rioclaro_invoice_inbox.company_id"
_TELEGRAM_ALLOWED_PARAM = "rioclaro_invoice_inbox.telegram_allowed_user_ids"
_TELEGRAM_OFFSET_PARAM = "rioclaro_invoice_inbox.telegram_update_offset"
_TELEGRAM_POLLING_PARAM = "rioclaro_invoice_inbox.telegram_polling_enabled"
_EXTRACTABLE_MIMETYPES = {"application/pdf", "image/jpeg", "image/png"}


class SupplierInvoiceIntake(models.Model):
    _name = "rioclaro.invoice.intake"
    _description = "Supplier Invoice Email"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "received_at desc, id desc"

    name = fields.Char(required=True, tracking=True)
    source_message_id = fields.Char(required=True, readonly=True, copy=False, index=True)
    sender_email = fields.Char(readonly=True, copy=False)
    source_mailbox = fields.Char(
        string="Received In",
        readonly=True,
        copy=False,
        index=True,
    )
    received_at = fields.Datetime(
        required=True,
        readonly=True,
        copy=False,
        default=fields.Datetime.now,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Vendor",
        tracking=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    supplier_rfc = fields.Char(string="Supplier RFC", tracking=True)
    invoice_number = fields.Char(string="Invoice Number", tracking=True)
    invoice_date = fields.Date(string="Invoice Date", tracking=True)
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    amount_untaxed = fields.Monetary(
        string="Subtotal",
        currency_field="currency_id",
        tracking=True,
    )
    amount_tax = fields.Monetary(
        string="Taxes",
        currency_field="currency_id",
        tracking=True,
    )
    amount_total = fields.Monetary(
        string="Total",
        currency_field="currency_id",
        tracking=True,
    )
    extraction_state = fields.Selection(
        [
            ("pending", "Pending Extraction"),
            ("extracted", "Extracted"),
            ("error", "Extraction Error"),
        ],
        string="Extraction Status",
        default="pending",
        required=True,
        tracking=True,
        copy=False,
    )
    invoice_id = fields.Many2one(
        "account.move",
        string="Vendor Bill",
        readonly=True,
        copy=False,
    )
    state = fields.Selection(
        [
            ("received", "Received"),
            ("in_review", "In Review"),
            ("invoiced", "Invoiced"),
            ("rejected", "Rejected"),
        ],
        default="received",
        required=True,
        tracking=True,
        index=True,
    )
    extracted_line_ids = fields.One2many(
        "rioclaro.invoice.intake.line",
        "intake_id",
        string="Extracted Lines",
        copy=False,
    )

    _source_message_id_unique = models.Constraint(
        "unique(source_message_id)",
        "This email has already been received.",
    )

    @api.onchange("supplier_rfc")
    def _onchange_supplier_rfc(self) -> None:
        """Attempt to match an existing vendor partner by RFC (VAT number)."""
        if self.supplier_rfc:
            normalized_rfc = self.supplier_rfc.strip().upper()
            partner = self.env["res.partner"].search(
                [
                    ("vat", "=ilike", normalized_rfc),
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "=", self.company_id.id),
                ],
                limit=1,
            )
            if partner:
                self.partner_id = partner

    @api.onchange("partner_id")
    def _onchange_partner_id(self) -> None:
        """Autofill supplier RFC from partner VAT if not already set."""
        if self.partner_id and self.partner_id.vat and not self.supplier_rfc:
            self.supplier_rfc = self.partner_id.vat

    @api.model
    def _get_source_message_id(self, msg_dict: dict) -> str:
        """Generate or retrieve a unique message identifier from email attributes.

        Args:
            msg_dict (dict): Dictionary representing parsed incoming email data.

        Returns:
            str: The source Message-ID or a deterministic SHA-256 fallback hash.
        """
        message_id = (msg_dict.get("message_id") or "").strip()
        if message_id:
            return message_id

        attachment_digests = []
        for attachment in msg_dict.get("attachments", []):
            content = attachment[1] or b""
            if isinstance(content, str):
                content = content.encode()
            attachment_digests.append(
                [attachment[0], sha256(content).hexdigest()]
            )
        fallback = {
            "sender": msg_dict.get("email_from", ""),
            "subject": msg_dict.get("subject", ""),
            "date": str(msg_dict.get("date") or ""),
            "attachments": attachment_digests,
        }
        return "generated:" + sha256(
            json.dumps(fallback, sort_keys=True).encode()
        ).hexdigest()

    @api.model
    def message_new(self, msg_dict: dict, custom_values: dict | None = None) -> models.Model:
        """Create a new SupplierInvoiceIntake record from an incoming email.

        Handles idempotency, company resolution, and fetchmail server tracking.

        Args:
            msg_dict (dict): Dictionary containing incoming message headers and content.
            custom_values (dict, optional): Extra field overrides for record creation.

        Returns:
            SupplierInvoiceIntake: The created or matched existing intake record.

        Raises:
            UserError: If the configured company or mail server does not exist.
        """
        source_message_id = self._get_source_message_id(msg_dict)
        existing = self.search(
            [("source_message_id", "=", source_message_id)],
            limit=1,
        )
        if existing:
            return existing

        company_id = self.env["ir.config_parameter"].sudo().get_int(
            _INTAKE_COMPANY_PARAM,
            default=self.env.company.id,
        )
        company = self.env["res.company"].sudo().browse(company_id).exists()
        if not company:
            raise UserError(
                _("The configured company for supplier invoice emails does not exist.")
            )

        values = {
            "name": msg_dict.get("subject") or _("Supplier invoice email"),
            "source_message_id": source_message_id,
            "sender_email": msg_dict.get("email_from"),
            "company_id": company.id,
        }
        fetchmail_server_id = self.env.context.get("default_fetchmail_server_id")
        if fetchmail_server_id:
            fetchmail_server = (
                self.env["fetchmail.server"]
                .sudo()
                .browse(fetchmail_server_id)
                .exists()
            )
            if not fetchmail_server:
                raise UserError(_("The incoming mail server no longer exists."))
            values["source_mailbox"] = fetchmail_server.name
        if custom_values:
            values.update(custom_values)
        return self.create(values)

    @api.model
    def _is_telegram_sender_allowed(self, sender_id: int) -> bool:
        """Return whether a Telegram user ID is in the configured allowlist.

        The allowlist is the comma-separated system parameter
        ``rioclaro_invoice_inbox.telegram_allowed_user_ids``. An empty or
        missing parameter allows nobody.
        """
        raw_ids = self.env["ir.config_parameter"].sudo().get_str(
            _TELEGRAM_ALLOWED_PARAM, ""
        )
        allowed_ids = {item.strip() for item in raw_ids.split(",") if item.strip()}
        return str(sender_id) in allowed_ids

    @api.model
    def create_from_telegram(
        self,
        message: TelegramMessage,
        content: bytes,
    ) -> models.Model:
        """Create an intake record from a Telegram message and its file.

        Messages from senders outside the allowlist are ignored. Repeated
        deliveries of the same chat/message pair return the existing record
        and do not duplicate the attachment.

        Args:
            message: The parsed Telegram message with its file metadata.
            content: The downloaded file bytes.

        Returns:
            The created or existing intake record, or an empty recordset when
            the sender is not allowed.
        """
        if not self._is_telegram_sender_allowed(message.sender_id):
            return self.browse()
        source_message_id = f"telegram:{message.chat_id}:{message.message_id}"
        existing = self.search([("source_message_id", "=", source_message_id)], limit=1)
        if existing:
            return existing

        company_id = self.env["ir.config_parameter"].sudo().get_int(
            _INTAKE_COMPANY_PARAM,
            default=self.env.company.id,
        )
        company = self.env["res.company"].sudo().browse(company_id).exists()
        if not company:
            raise UserError(
                _("The configured company for supplier invoice emails does not exist.")
            )

        record = self.create(
            {
                "name": message.caption
                or _("Telegram invoice from %s", message.sender_name),
                "source_message_id": source_message_id,
                "sender_email": f"telegram:{message.sender_id}",
                "source_mailbox": "Telegram",
                "company_id": company.id,
            }
        )
        self.env["ir.attachment"].create(
            {
                "name": message.file.file_name,
                "mimetype": message.file.mimetype,
                "raw": content,
                "res_model": self._name,
                "res_id": record.id,
            }
        )
        record.message_post(
            body=_("Received from Telegram user %s.", message.sender_name)
        )
        return record

    @api.model
    def cron_poll_telegram(self) -> int:
        """Poll the Telegram bot and create intake records for new invoice files.

        Called by the scheduled action. Polling runs only in databases where
        ``rioclaro_invoice_inbox.telegram_polling_enabled`` is true, so test or
        copied databases never compete with the real one for the same bot.
        Updates are acknowledged only after the
        whole batch was processed, so a failed download is retried on the next
        run (already created records are deduplicated).

        Returns:
            The number of intake records created in this run.
        """
        token = os.environ.get("RIOCLARO_TELEGRAM_BOT_TOKEN", "")
        if not token:
            _logger.info("Telegram polling skipped: bot token is not configured.")
            return 0

        params = self.env["ir.config_parameter"].sudo()
        if not params.get_bool(_TELEGRAM_POLLING_PARAM):
            _logger.info("Telegram polling skipped: it is not enabled in this database.")
            return 0
        client = TelegramClient(token)
        try:
            offset_value = params.get_int(_TELEGRAM_OFFSET_PARAM, default=0)
            next_offset, messages = client.get_updates(offset_value or None)
        except TelegramError:
            _logger.warning("Telegram polling failed while reading updates.")
            return 0

        created = 0
        for message in messages:
            try:
                if not self._is_telegram_sender_allowed(message.sender_id):
                    client.send_message(message.chat_id, "No autorizado.")
                    continue
                source_id = f"telegram:{message.chat_id}:{message.message_id}"
                is_new = not self.search_count([("source_message_id", "=", source_id)])
                record = self.create_from_telegram(
                    message, client.download_file(message.file.file_id)
                )
                if record and is_new:
                    created += 1
                    client.send_message(
                        message.chat_id, f"Recibido. Registro: {record.name}"
                    )
            except TelegramError:
                _logger.warning(
                    "Telegram message %s could not be processed; it will be retried.",
                    message.message_id,
                )
                return created

        if next_offset is not None:
            params.set_int(_TELEGRAM_OFFSET_PARAM, next_offset)
        return created

    def action_mark_in_review(self) -> bool:
        """Move intake record status from 'received' to 'in_review'."""
        self.filtered(lambda record: record.state == "received").write(
            {"state": "in_review"}
        )
        return True

    def action_reject(self) -> bool:
        """Mark intake record as rejected."""
        self.filtered(lambda record: record.state not in ("rejected", "invoiced")).write(
            {"state": "rejected"}
        )
        return True
    
    def _get_extractable_attachment(self):
        """Return the first PDF/JPEG/PNG attachment of this intake, or an empty recordset."""
        self.ensure_one()
        attachments = self.env["ir.attachment"].search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
        ]) | self.message_ids.attachment_ids
        for attachment in attachments.sorted("id"):
            if attachment.mimetype in _EXTRACTABLE_MIMETYPES:
                return attachment
        return self.env["ir.attachment"]

    def action_extract_invoice_data(self) -> bool:
        """Send the first supported attachment to Azure and store the extracted data.

        Configuration or attachment problems raise a UserError without changing
        data. Azure failures set the extraction status to 'error' and log the
        reason in the chatter, so the email and its attachments are never lost.
        """
        self.ensure_one()
        endpoint = os.environ.get("RIOCLARO_DOCUMENT_INTELLIGENCE_ENDPOINT", "")
        api_key = os.environ.get("RIOCLARO_DOCUMENT_INTELLIGENCE_KEY", "")
        if not endpoint or not api_key:
            raise UserError(_("Azure Document Intelligence is not configured."))
        attachment = self._get_extractable_attachment()
        if not attachment:
            raise UserError(_("No PDF, JPEG or PNG attachment found to extract."))

        client = AzureDocumentIntelligenceClient(endpoint, api_key)
        try:
            result = client.analyze_invoice(
                attachment.raw, attachment.mimetype
            )
        except DocumentIntelligenceError as error:
            self.extraction_state = "error"
            self.message_post(body=_("Invoice extraction failed: %s", str(error)))
            return False

        values = {
            "supplier_rfc": result.supplier_rfc or self.supplier_rfc,
            "invoice_number": result.invoice_number or self.invoice_number,
            "invoice_date": result.invoice_date or self.invoice_date,
            "amount_untaxed": result.amount_untaxed or 0.0,
            "amount_tax": result.amount_tax or 0.0,
            "amount_total": result.amount_total or 0.0,
            "extraction_state": "extracted",
        }
        currency = self.env["res.currency"].search(
            [("name", "=", result.currency_code or "")], limit=1
        )
        if currency:
            values["currency_id"] = currency.id
        self.write(values)
        self.extracted_line_ids.unlink()
        self.env["rioclaro.invoice.intake.line"].create([
            {
                "intake_id": self.id,
                "description": line.description or _("Line"),
                "quantity": line.quantity or 0.0,
                "unit_price": line.unit_price or 0.0,
                "amount": line.amount or 0.0,
                "confidence": line.confidence or 0.0,
            }
            for line in result.lines
        ])
        return True
    
    def action_create_vendor_bill(self) -> dict:
        """Create a draft vendor bill in account.move from this intake record.

        Validates that a bill does not already exist, resolves or creates the vendor partner,
        attaches original incoming attachments, and updates state to 'invoiced'.

        Returns:
            dict: Window action dictionary to display the newly created draft vendor bill.

        Raises:
            UserError: If the intake is rejected, already invoiced, or missing vendor information.
        """
        self.ensure_one()
        if self.invoice_id:
            raise UserError(_("A vendor bill has already been created for this intake."))
        if self.state == "rejected":
            raise UserError(_("Cannot create a vendor bill for a rejected intake."))

        partner = self.partner_id
        if not partner and self.supplier_rfc:
            normalized_rfc = self.supplier_rfc.strip().upper()
            partner = self.env["res.partner"].search(
                [
                    ("vat", "=ilike", normalized_rfc),
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "=", self.company_id.id),
                ],
                limit=1,
            )
            if not partner:
                partner = self.env["res.partner"].create({
                    "name": self.name or normalized_rfc,
                    "vat": normalized_rfc,
                    "company_id": self.company_id.id,
                })
            self.partner_id = partner

        if not partner:
            raise UserError(
                _("Please specify a Vendor or Supplier RFC before creating a vendor bill.")
            )

        bill_vals: dict = {
            "move_type": "in_invoice",
            "partner_id": partner.id,
            "company_id": self.company_id.id,
            "currency_id": self.currency_id.id,
            "ref": self.invoice_number or self.name,
            "invoice_date": self.invoice_date or fields.Date.context_today(self),
        }
        
        if self.extracted_line_ids:
            bill_vals["invoice_line_ids"] = [
                (
                    0,
                    0,
                    {
                        "name": line.description,
                        "quantity": line.quantity or 1.0,
                        "price_unit": line.unit_price
                        or (line.amount / (line.quantity or 1.0)),
                    },
                )
                for line in self.extracted_line_ids
            ]
        elif (self.amount_untaxed or self.amount_total) > 0:
            line_price = self.amount_untaxed or self.amount_total
            bill_vals["invoice_line_ids"] = [
                (
                    0,
                    0,
                    {
                        "name": self.name or _("Supplier Invoice Intake Item"),
                        "price_unit": line_price,
                        "quantity": 1.0,
                    },
                )
            ]

        bill = self.env["account.move"].create(bill_vals)

        # Copy original attachments to the vendor bill
        attachments = self.env["ir.attachment"].search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
        ]) | self.message_ids.attachment_ids
        for attachment in attachments:
            attachment.copy({
                "res_model": "account.move",
                "res_id": bill.id,
            })

        self.write({
            "invoice_id": bill.id,
            "state": "invoiced",
        })
        self.message_post(
            body=_("Draft vendor bill created: %s") % (bill.display_name or bill.name)
        )

        return {
            "name": _("Vendor Bill"),
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": bill.id,
            "target": "current",
        }

    def action_view_vendor_bill(self) -> dict:
        """Open the form view of the associated vendor bill.

        Returns:
            dict: Window action dictionary to view the bill.

        Raises:
            UserError: If no vendor bill is linked to this record.
        """
        self.ensure_one()
        if not self.invoice_id:
            raise UserError(_("No vendor bill is associated with this intake."))
        return {
            "name": _("Vendor Bill"),
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.invoice_id.id,
            "target": "current",
        }
