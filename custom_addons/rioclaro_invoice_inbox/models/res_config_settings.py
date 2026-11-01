from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """Expose the Telegram query rate limit in Settings."""

    _inherit = "res.config.settings"

    rioclaro_telegram_query_rate_limit = fields.Integer(
        string="Telegram queries per contact",
        config_parameter="rioclaro_invoice_inbox.telegram_query_rate_limit",
        default=5,
        help="Maximum queries a Telegram contact may send within the time window.",
    )
    rioclaro_telegram_query_window_minutes = fields.Integer(
        string="Telegram rate-limit window (minutes)",
        config_parameter="rioclaro_invoice_inbox.telegram_query_window_minutes",
        default=5,
        help="Length of the rolling window used to count a contact's queries.",
    )
