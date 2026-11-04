from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .telegram_response_style import (
    DEFAULT_TELEGRAM_RESPONSE_STYLE,
    MAX_CUSTOM_ONBOARDING_GREETING_LENGTH,
    MAX_CUSTOM_RESPONSE_STYLE_LENGTH,
    TELEGRAM_CUSTOM_RESPONSE_STYLE_PARAM,
    TELEGRAM_ONBOARDING_GREETING_PARAM,
    TELEGRAM_RESPONSE_STYLE_SELECTION,
    TELEGRAM_RESPONSE_STYLE_PARAM,
)


class ResConfigSettings(models.TransientModel):
    """Expose the Telegram query rate limit in Settings."""

    _inherit = "res.config.settings"

    rioclaro_telegram_query_rate_limit = fields.Integer(
        string="Telegram queries per user",
        config_parameter="rioclaro_invoice_inbox.telegram_query_rate_limit",
        default=5,
        help="Maximum queries a Telegram user may send within the time window.",
    )
    rioclaro_telegram_query_window_minutes = fields.Integer(
        string="Telegram rate-limit window (minutes)",
        config_parameter="rioclaro_invoice_inbox.telegram_query_window_minutes",
        default=5,
        help="Length of the rolling window used to count a user's queries.",
    )
    rioclaro_telegram_response_style = fields.Selection(
        TELEGRAM_RESPONSE_STYLE_SELECTION,
        string="Telegram response style",
        config_parameter=TELEGRAM_RESPONSE_STYLE_PARAM,
        default=DEFAULT_TELEGRAM_RESPONSE_STYLE,
        help=(
            "Controls the first-contact name prompt and the tone of "
            "AI-generated Telegram answers."
        ),
    )
    rioclaro_telegram_custom_response_style = fields.Char(
        string="Custom response style instructions",
        config_parameter=TELEGRAM_CUSTOM_RESPONSE_STYLE_PARAM,
        help=(
            "Optional guidance for the tone and presentation of AI-generated "
            "Telegram answers. It cannot change facts or access rules."
        ),
    )
    rioclaro_telegram_onboarding_greeting = fields.Char(
        string="Custom first-contact greeting",
        config_parameter=TELEGRAM_ONBOARDING_GREETING_PARAM,
        help=(
            "Optional replacement for the first name prompt. Leave blank to use "
            "the greeting from the selected response style."
        ),
    )

    @api.constrains(
        "rioclaro_telegram_custom_response_style",
        "rioclaro_telegram_onboarding_greeting",
    )
    def _check_telegram_response_text_lengths(self) -> None:
        """Bound administrator-provided prompt text and welcome messages."""
        for settings in self:
            if (
                len(settings.rioclaro_telegram_custom_response_style or "")
                > MAX_CUSTOM_RESPONSE_STYLE_LENGTH
            ):
                raise ValidationError(
                    _(
                        "Custom Telegram response style instructions must be "
                        "500 characters or fewer."
                    )
                )
            if (
                len(settings.rioclaro_telegram_onboarding_greeting or "")
                > MAX_CUSTOM_ONBOARDING_GREETING_LENGTH
            ):
                raise ValidationError(
                    _(
                        "The custom Telegram first-contact greeting must be "
                        "500 characters or fewer."
                    )
                )
