"""Shared Telegram response-style presets and configuration limits."""

from dataclasses import dataclass

DEFAULT_TELEGRAM_RESPONSE_STYLE = "friendly"
TELEGRAM_RESPONSE_STYLE_PARAM = (
    "rioclaro_invoice_inbox.telegram_response_style"
)
TELEGRAM_CUSTOM_RESPONSE_STYLE_PARAM = (
    "rioclaro_invoice_inbox.telegram_custom_response_style"
)
TELEGRAM_ONBOARDING_GREETING_PARAM = (
    "rioclaro_invoice_inbox.telegram_onboarding_greeting"
)
MAX_CUSTOM_RESPONSE_STYLE_LENGTH = 500
MAX_CUSTOM_ONBOARDING_GREETING_LENGTH = 500


@dataclass(frozen=True)
class TelegramResponseStyle:
    """AI style guidance and first-name prompt for one supported preset."""

    label: str
    ai_instruction: str
    onboarding_greeting: str


TELEGRAM_RESPONSE_STYLES: dict[str, TelegramResponseStyle] = {
    "friendly": TelegramResponseStyle(
        label="Amable y cercano",
        ai_instruction="Usa un tono cordial, cercano y natural.",
        onboarding_greeting=(
            "¡Hola! Para crear tu perfil de Telegram en Odoo, "
            "¿qué nombre quieres usar?"
        ),
    ),
    "formal": TelegramResponseStyle(
        label="Formal y profesional",
        ai_instruction="Usa un tono formal, respetuoso y profesional.",
        onboarding_greeting=(
            "Bienvenido/a. Para crear su perfil de Telegram en Odoo, "
            "¿qué nombre prefiere que usemos?"
        ),
    ),
    "concise": TelegramResponseStyle(
        label="Breve y directo",
        ai_instruction="Responde de forma breve y directa, sin relleno.",
        onboarding_greeting=(
            "¡Hola! ¿Qué nombre usamos para tu perfil de Telegram?"
        ),
    ),
    "detailed": TelegramResponseStyle(
        label="Detallado y explicativo",
        ai_instruction=(
            "Explica la respuesta con claridad y contexto útil, sin repetir "
            "información ni añadir hechos."
        ),
        onboarding_greeting=(
            "¡Hola! Para que podamos identificarte en Odoo, necesitamos crear "
            "un perfil. ¿Qué nombre te gustaría usar?"
        ),
    ),
}

TELEGRAM_RESPONSE_STYLE_SELECTION = [
    (style_key, style.label)
    for style_key, style in TELEGRAM_RESPONSE_STYLES.items()
]


def get_telegram_response_style(style_key: str) -> TelegramResponseStyle:
    """Return a configured preset or report an invalid setting explicitly."""
    try:
        return TELEGRAM_RESPONSE_STYLES[style_key]
    except KeyError as error:
        raise ValueError(f"Unsupported Telegram response style: {style_key}") from error
