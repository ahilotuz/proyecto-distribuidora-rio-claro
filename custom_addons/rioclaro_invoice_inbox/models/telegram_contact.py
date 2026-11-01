from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

PERMISSION_FIELDS: tuple[str, ...] = (
    "can_query_inventory",
    "can_query_purchases",
    "can_query_sales",
    "can_query_billing",
    "can_query_margins",
    "can_check_order_status",
    "can_request_quotation",
    "can_submit_vendor_invoice",
    "can_issue_customer_invoice",
)

# Default permissions granted when a role is selected. Administrators can
# adjust each contact afterwards.
ROLE_DEFAULT_PERMISSIONS: dict[str, frozenset[str]] = {
    "supplier": frozenset(
        {
            "can_query_purchases",
            "can_query_billing",
            "can_submit_vendor_invoice",
        }
    ),
    "customer": frozenset(
        {
            "can_query_sales",
            "can_query_billing",
            "can_check_order_status",
            "can_request_quotation",
        }
    ),
    "internal": frozenset(PERMISSION_FIELDS),
}


class TelegramContact(models.Model):
    """A Telegram user authorized to talk to the bot, with explicit permissions.

    Access is denied by default: a Telegram ID without an active contact, or a
    contact without the requested permission, is never served.
    """

    _name = "rioclaro.telegram.contact"
    _description = "Authorized Telegram Contact"
    _order = "name"

    name = fields.Char(required=True)
    telegram_user_id = fields.Char(string="Telegram User ID", required=True, index=True)
    role = fields.Selection(
        [
            ("supplier", "Supplier"),
            ("internal", "Internal user"),
            ("customer", "Customer"),
        ],
        required=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
        help="Limits supplier and customer answers to this partner's own data.",
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)

    can_query_inventory = fields.Boolean(string="Query inventory")
    can_query_purchases = fields.Boolean(string="Query purchases")
    can_query_sales = fields.Boolean(string="Query sales")
    can_query_billing = fields.Boolean(string="Query billing")
    can_query_margins = fields.Boolean(string="Query margins and costs")
    can_check_order_status = fields.Boolean(string="Check order status")
    can_request_quotation = fields.Boolean(string="Request quotation")
    can_submit_vendor_invoice = fields.Boolean(string="Submit vendor invoice")
    can_issue_customer_invoice = fields.Boolean(string="Issue customer invoice")

    _telegram_user_id_unique = models.Constraint(
        "unique(telegram_user_id)",
        "This Telegram user already has a contact.",
    )

    @api.constrains("telegram_user_id")
    def _check_telegram_user_id(self) -> None:
        """Accept only numeric Telegram IDs (never usernames, which can change)."""
        for contact in self:
            if not contact.telegram_user_id.isdigit():
                raise ValidationError(
                    _("The Telegram user ID must contain digits only.")
                )

    @api.constrains("role", "partner_id")
    def _check_partner_for_external_roles(self) -> None:
        """Suppliers and customers must be linked to a partner to scope their data."""
        for contact in self:
            if contact.role in ("supplier", "customer") and not contact.partner_id:
                raise ValidationError(
                    _("Suppliers and customers must be linked to a partner.")
                )

    @api.model
    def _role_default_values(self, role: str) -> dict[str, bool]:
        """Return the permission flags a role grants by default."""
        granted = ROLE_DEFAULT_PERMISSIONS.get(role, frozenset())
        return {field: field in granted for field in PERMISSION_FIELDS}

    @api.onchange("role")
    def _onchange_role(self) -> None:
        """Preload the role's default permissions in the form."""
        if self.role:
            for field, value in self._role_default_values(self.role).items():
                self[field] = value

    @api.model_create_multi
    def create(self, vals_list: list[dict]) -> models.Model:
        """Apply the role's default permissions unless explicitly provided."""
        for vals in vals_list:
            if vals.get("role"):
                for field, value in self._role_default_values(vals["role"]).items():
                    vals.setdefault(field, value)
        return super().create(vals_list)

    @api.model
    def get_active_contact(self, telegram_user_id: int | str) -> models.Model:
        """Return the active contact for a Telegram ID, or an empty recordset."""
        return self.sudo().search(
            [("telegram_user_id", "=", str(telegram_user_id)), ("active", "=", True)],
            limit=1,
        )

    @api.model
    def is_allowed(self, telegram_user_id: int | str, permission: str) -> bool:
        """Return whether an active contact holds a permission (deny by default).

        Args:
            telegram_user_id: The numeric Telegram user ID of the sender.
            permission: One of ``PERMISSION_FIELDS``.
        """
        if permission not in PERMISSION_FIELDS:
            return False
        contact = self.get_active_contact(telegram_user_id)
        return bool(contact and contact[permission])
