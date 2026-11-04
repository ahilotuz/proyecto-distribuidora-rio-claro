"""Tests for globally configurable Telegram response styles."""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.rioclaro_invoice_inbox.models.telegram_response_style import (
    MAX_CUSTOM_ONBOARDING_GREETING_LENGTH,
    MAX_CUSTOM_RESPONSE_STYLE_LENGTH,
    TELEGRAM_CUSTOM_RESPONSE_STYLE_PARAM,
    TELEGRAM_ONBOARDING_GREETING_PARAM,
    TELEGRAM_RESPONSE_STYLE_PARAM,
)


@tagged("post_install", "-at_install")
class TestTelegramResponseStyleSettings(TransactionCase):
    def setUp(self):
        super().setUp()
        self.settings_model = self.env["res.config.settings"]
        self.parameters = self.env["ir.config_parameter"].sudo()

    def test_settings_save_preset_custom_style_and_greeting(self):
        settings = self.settings_model.create(
            {
                "rioclaro_telegram_response_style": "formal",
                "rioclaro_telegram_custom_response_style": (
                    "Usa español mexicano y frases claras."
                ),
                "rioclaro_telegram_onboarding_greeting": (
                    "¡Hola! ¿Qué nombre prefieres?"
                ),
            }
        )

        settings.execute()

        self.assertEqual(
            self.parameters.get_str(TELEGRAM_RESPONSE_STYLE_PARAM), "formal"
        )
        self.assertEqual(
            self.parameters.get_str(TELEGRAM_CUSTOM_RESPONSE_STYLE_PARAM),
            "Usa español mexicano y frases claras.",
        )
        self.assertEqual(
            self.parameters.get_str(TELEGRAM_ONBOARDING_GREETING_PARAM),
            "¡Hola! ¿Qué nombre prefieres?",
        )

    def test_overlong_custom_style_is_rejected(self):
        settings = self.settings_model.create({})

        with self.assertRaises(ValidationError):
            settings.write(
                {
                    "rioclaro_telegram_custom_response_style": (
                        "x" * (MAX_CUSTOM_RESPONSE_STYLE_LENGTH + 1)
                    )
                }
            )

    def test_overlong_custom_greeting_is_rejected(self):
        settings = self.settings_model.create({})

        with self.assertRaises(ValidationError):
            settings.write(
                {
                    "rioclaro_telegram_onboarding_greeting": (
                        "x" * (MAX_CUSTOM_ONBOARDING_GREETING_LENGTH + 1)
                    )
                }
            )

    def test_onboarding_prompt_uses_selected_preset_and_custom_override(self):
        contact = self.env["rioclaro.telegram.contact"].create(
            {
                "name": "New sender",
                "telegram_user_id": "991",
                "role": "public",
                "onboarding_state": "awaiting_name",
            }
        )
        intake = self.env["rioclaro.invoice.intake"]
        self.parameters.set_str(TELEGRAM_RESPONSE_STYLE_PARAM, "concise")

        self.assertEqual(
            intake._telegram_onboarding_prompt(contact),
            "¡Hola! ¿Qué nombre usamos para tu perfil de Telegram?",
        )

        self.parameters.set_str(
            TELEGRAM_ONBOARDING_GREETING_PARAM, "¡Buen día! ¿Cómo te llamas?"
        )
        self.assertEqual(
            intake._telegram_onboarding_prompt(contact),
            "¡Buen día! ¿Cómo te llamas?",
        )
