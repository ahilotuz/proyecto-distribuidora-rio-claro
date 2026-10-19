import json
from hashlib import sha256

from odoo import _, api, fields, models
from odoo.exceptions import UserError


_INTAKE_COMPANY_PARAM = "rioclaro_invoice_inbox.company_id"


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

        line_price = self.amount_untaxed or self.amount_total
        if line_price and line_price > 0:
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
