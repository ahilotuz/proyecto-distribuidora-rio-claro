import json
import logging
import os
from datetime import timedelta
from hashlib import sha256
from typing import Literal

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.mail import email_normalize

from .chat_help import is_capability_help_request
from .document_intelligence import (
    AzureDocumentIntelligenceClient,
    DocumentIntelligenceError,
)
from .public_product_links import link_public_products
from .speech_client import AzureSpeechClient, SpeechError
from .telegram_client import TelegramClient, TelegramError, TelegramMessage

_logger = logging.getLogger(__name__)


_INTAKE_COMPANY_PARAM = "rioclaro_invoice_inbox.company_id"
_TELEGRAM_OFFSET_PARAM = "rioclaro_invoice_inbox.telegram_update_offset"
_TELEGRAM_POLLING_PARAM = "rioclaro_invoice_inbox.telegram_polling_enabled"
_EXTRACTABLE_MIMETYPES = {"application/pdf", "image/jpeg", "image/png"}
_TELEGRAM_RATE_LIMIT_PARAM = "rioclaro_invoice_inbox.telegram_query_rate_limit"
_TELEGRAM_RATE_WINDOW_PARAM = "rioclaro_invoice_inbox.telegram_query_window_minutes"
_TELEGRAM_QUERY_RATE_LIMIT = 5
_TELEGRAM_QUERY_WINDOW_MINUTES = 5
_TELEGRAM_MAX_REPLY_LENGTH = 4000
_TELEGRAM_NAME_MAX_LENGTH = 100
_TELEGRAM_EMAIL_SKIP_RESPONSES = {
    "no",
    "no gracias",
    "omitir",
    "omitir correo",
    "prefiero no",
    "prefiero no compartirlo",
    "sin correo",
    "skip",
}
_TELEGRAM_PRIVATE_CAPABILITY_LABELS = (
    ("can_query_inventory", "Consultar existencias y disponibilidad de productos"),
    ("can_query_purchases", "Consultar compras"),
    ("can_query_sales", "Consultar ventas"),
    ("can_query_billing", "Consultar facturas y saldos"),
    ("can_query_margins", "Consultar márgenes y costos"),
    ("can_check_order_status", "Consultar el estado de pedidos"),
)


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
        """Return whether a Telegram user may submit vendor invoices.

        Delegates to the Telegram contact ACL: the sender needs an active
        contact holding the ``can_submit_vendor_invoice`` permission. Unknown
        senders are denied.
        """
        return self.env["rioclaro.telegram.contact"].is_allowed(
            sender_id, "can_submit_vendor_invoice"
        )

    @api.model
    def _telegram_help_response(self, contact: models.Model | None = None) -> str:
        """Describe public features and only private features enabled for contact."""
        response_lines = [
            "Puedo recomendar productos del catálogo publicado, consultar sus "
            "precios públicos y responder preguntas sobre información publicada "
            "en el sitio web."
        ]
        if not contact:
            response_lines.extend(
                [
                    "",
                    "Puedes consultar esta información pública sin registrar "
                    "una cuenta.",
                    "Las consultas de datos privados y el envío de facturas requieren "
                    "una cuenta autorizada.",
                    "Escribe /ayuda en cualquier momento.",
                ]
            )
            return "\n".join(response_lines)

        response_lines.extend(["", "Para tu cuenta están habilitadas estas funciones:"])
        capabilities = [
            label
            for permission, label in _TELEGRAM_PRIVATE_CAPABILITY_LABELS
            if contact[permission]
        ]
        if contact.can_submit_vendor_invoice:
            capabilities.append("Enviar facturas de proveedor para revisión")
        response_lines.extend(
            f"• {capability}" for capability in capabilities
        )
        if not capabilities:
            response_lines.append(
                "• No tienes habilitadas consultas de datos privados ni el envío "
                "de facturas."
            )
        response_lines.extend(
            [
                "",
                "Las consultas privadas se limitan a los permisos y registros "
                "asignados a tu cuenta.",
                "Puedes escribir /ayuda en cualquier momento. También puedes "
                "enviar preguntas por texto o nota de voz.",
            ]
        )
        return "\n".join(response_lines)

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
        """Poll Telegram for invoice files and private text queries.

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

        if os.environ.get("RIOCLARO_TELEGRAM_WEBHOOK_SECRET", ""):
            _logger.info("Telegram polling skipped: webhook mode is configured.")
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
                created += self._handle_telegram_message(client, message)
            except TelegramError:
                _logger.warning(
                    "Telegram message %s could not be processed; it will be retried.",
                    message.message_id,
                )
                return created

        if next_offset is not None:
            params.set_int(_TELEGRAM_OFFSET_PARAM, next_offset)
        return created

    @api.model
    def _is_telegram_rate_limited(self, telegram_user_id: int | str) -> bool:
        """Return whether this Telegram user reached the rolling query limit."""
        params = self.env["ir.config_parameter"].sudo()
        rate_limit = max(
            params.get_int(
                _TELEGRAM_RATE_LIMIT_PARAM,
                default=_TELEGRAM_QUERY_RATE_LIMIT,
            ),
            1,
        )
        window_minutes = max(
            params.get_int(
                _TELEGRAM_RATE_WINDOW_PARAM,
                default=_TELEGRAM_QUERY_WINDOW_MINUTES,
            ),
            1,
        )
        window_start = fields.Datetime.now() - timedelta(minutes=window_minutes)
        recent_count = self.env["rioclaro.assisted.query.audit"].sudo().search_count(
            [
                ("telegram_user_id", "=", str(telegram_user_id)),
                ("request_at", ">=", window_start),
            ]
        )
        return recent_count >= rate_limit

    @api.model
    def _telegram_onboarding_prompt(self, contact: models.Model) -> str:
        """Return the next onboarding prompt for a Telegram contact."""
        if contact.onboarding_state == "awaiting_name":
            return (
                "¡Hola! Para crear tu perfil de Telegram en Odoo, "
                "¿qué nombre quieres usar?"
            )
        if contact.onboarding_state == "awaiting_email":
            return (
                f"Gracias, {contact.name}. ¿Cuál es tu correo electrónico? "
                "Es opcional; si lo compartes, se guardará en Odoo y lo verán "
                "los administradores. Escribe solo el correo o «Omitir» para "
                "continuar sin compartirlo."
            )
        return self._telegram_help_response(contact)

    @api.model
    def _process_telegram_onboarding(
        self,
        client: TelegramClient,
        message: TelegramMessage,
        contact: models.Model,
        *,
        start: bool = False,
    ) -> None:
        """Collect a name and optional email without processing the message as a query."""
        audit_model = self.env["rioclaro.assisted.query.audit"].sudo()
        source_id = f"telegram:{message.chat_id}:{message.message_id}"
        input_type = "voice" if message.voice else "text"
        existing_audit = audit_model.search(
            [("source_message_id", "=", source_id)],
            limit=1,
        )
        if existing_audit:
            client.send_message(
                message.chat_id,
                self._telegram_onboarding_prompt(contact),
            )
            return

        if self._is_telegram_rate_limited(message.sender_id):
            audit_model.log_request(
                telegram_user_id=message.sender_id,
                source_message_id=source_id,
                outcome="rate_limited",
                contact=contact,
                tool="onboarding",
                input_type=input_type,
            )
            client.send_message(
                message.chat_id,
                "Límite alcanzado: intenta de nuevo en unos minutos.",
            )
            return

        if start:
            audit_model.log_request(
                telegram_user_id=message.sender_id,
                source_message_id=source_id,
                outcome="allowed",
                contact=contact,
                tool="onboarding",
                input_type=input_type,
            )
            client.send_message(
                message.chat_id,
                self._telegram_onboarding_prompt(contact),
            )
            return

        response_text = message.text.strip()
        if message.voice:
            duration = message.voice.duration_seconds
            if duration is not None and duration > 60:
                audit_model.log_request(
                    telegram_user_id=message.sender_id,
                    source_message_id=source_id,
                    outcome="invalid",
                    contact=contact,
                    tool="onboarding",
                    input_type=input_type,
                )
                client.send_message(
                    message.chat_id,
                    "La nota de voz no puede superar 60 segundos. "
                    "Envía una más corta o escribe tu respuesta.",
                )
                return
            audio = client.download_file(message.voice.file_id)
            try:
                speech_client = AzureSpeechClient(
                    endpoint=os.environ.get("RIOCLARO_SPEECH_ENDPOINT", ""),
                    region=os.environ.get("RIOCLARO_SPEECH_REGION", ""),
                    api_key=os.environ.get("RIOCLARO_SPEECH_KEY", ""),
                )
                response_text = speech_client.transcribe(audio, language="es-MX")
            except (ValueError, SpeechError):
                audit_model.log_request(
                    telegram_user_id=message.sender_id,
                    source_message_id=source_id,
                    outcome="error",
                    contact=contact,
                    tool="onboarding",
                    input_type=input_type,
                )
                client.send_message(
                    message.chat_id,
                    "No pude entender la nota de voz. Intenta de nuevo o "
                    "responde por texto.",
                )
                return

        if contact.onboarding_state == "awaiting_name":
            name = " ".join(response_text.split())
            if (
                not name
                or name.startswith("/")
                or len(name) > _TELEGRAM_NAME_MAX_LENGTH
            ):
                audit_model.log_request(
                    telegram_user_id=message.sender_id,
                    source_message_id=source_id,
                    outcome="invalid",
                    contact=contact,
                    tool="onboarding",
                    input_type=input_type,
                )
                client.send_message(
                    message.chat_id,
                    f"Envía un nombre de hasta {_TELEGRAM_NAME_MAX_LENGTH} caracteres.",
                )
                return
            contact.write(
                {
                    "name": name,
                    "onboarding_state": "awaiting_email",
                    "onboarding_last_message_id": source_id,
                }
            )
            audit_model.log_request(
                telegram_user_id=message.sender_id,
                source_message_id=source_id,
                outcome="allowed",
                contact=contact,
                tool="onboarding",
                input_type=input_type,
            )
            client.send_message(
                message.chat_id,
                self._telegram_onboarding_prompt(contact),
            )
            return

        email_choice = " ".join(response_text.casefold().split()).strip(
            " \t\r\n.,;:¡!¿?"
        )
        if email_choice in _TELEGRAM_EMAIL_SKIP_RESPONSES:
            email = False
        else:
            email = email_normalize(response_text)
            if not email or len(email) > 254:
                audit_model.log_request(
                    telegram_user_id=message.sender_id,
                    source_message_id=source_id,
                    outcome="invalid",
                    contact=contact,
                    tool="onboarding",
                    input_type=input_type,
                )
                client.send_message(
                    message.chat_id,
                    "No pude validar ese correo. Escribe una dirección completa "
                    "o «Omitir».",
                )
                return

        contact.write(
            {
                "email": email,
                "onboarding_state": "complete",
                "onboarding_last_message_id": source_id,
            }
        )
        audit_model.log_request(
            telegram_user_id=message.sender_id,
            source_message_id=source_id,
            outcome="allowed",
            contact=contact,
            tool="onboarding",
            input_type=input_type,
        )
        client.send_message(
            message.chat_id,
            self._telegram_onboarding_prompt(contact),
        )

    @api.model
    def _handle_telegram_message(
        self, client: TelegramClient, message: TelegramMessage
    ) -> int:
        """Process one Telegram message (text/voice query or invoice file).

        Shared by polling and the webhook queue.

        Returns:
            ``1`` when a new intake record was created, otherwise ``0``.

        Raises:
            TelegramError: When a Telegram call fails and the message must be
                retried.
        """
        contact, created = self.env[
            "rioclaro.telegram.contact"
        ].get_or_create_public_contact(
            message.sender_id,
            message.sender_name,
            f"telegram:{message.chat_id}:{message.message_id}",
        )
        if not contact.active:
            client.send_message(
                message.chat_id,
                "El acceso a este bot está bloqueado. Contacta a un administrador.",
            )
            return 0

        source_id = f"telegram:{message.chat_id}:{message.message_id}"
        onboarding_audit = self.env["rioclaro.assisted.query.audit"].sudo().search(
            [
                ("source_message_id", "=", source_id),
                ("tool", "=", "onboarding"),
            ],
            limit=1,
        )
        if (
            created
            or contact.onboarding_state != "complete"
            or onboarding_audit
        ):
            is_start_message = created or (
                contact.onboarding_last_message_id == source_id
            )
            self._process_telegram_onboarding(
                client,
                message,
                contact,
                start=is_start_message,
            )
            return 0

        if message.text and not message.file:
            self._process_telegram_text_query(client, message)
            return 0
        if message.voice:
            if (
                message.voice.duration_seconds is not None
                and message.voice.duration_seconds > 60
            ):
                self._process_telegram_text_query(
                    client,
                    message,
                    voice_duration_seconds=message.voice.duration_seconds,
                )
                return 0
            voice_audio = client.download_file(message.voice.file_id)
            self._process_telegram_text_query(
                client,
                message,
                voice_audio=voice_audio,
                voice_duration_seconds=message.voice.duration_seconds,
            )
            return 0
        if not message.file:
            return 0
        if not self._is_telegram_sender_allowed(message.sender_id):
            client.send_message(message.chat_id, "No autorizado.")
            return 0
        source_id = f"telegram:{message.chat_id}:{message.message_id}"
        is_new = not self.search_count([("source_message_id", "=", source_id)])
        record = self.create_from_telegram(
            message, client.download_file(message.file.file_id)
        )
        if record and is_new:
            client.send_message(message.chat_id, f"Recibido. Registro: {record.name}")
            return 1
        return 0

    @api.model
    def _process_telegram_text_query(
        self,
        client: TelegramClient,
        message: TelegramMessage,
        *,
        voice_audio: bytes | None = None,
        voice_duration_seconds: int | None = None,
    ) -> None:
        """Authorize, rate-limit, audit, and answer one text or voice query."""
        input_type = "voice" if message.voice else "text"
        audit_model = self.env["rioclaro.assisted.query.audit"].sudo()
        source_id = f"telegram:{message.chat_id}:{message.message_id}"
        if audit_model.search_count([("source_message_id", "=", source_id)]):
            return

        contacts = self.env["rioclaro.telegram.contact"]
        contact = contacts.get_contact(message.sender_id)
        if not contact or not contact.active:
            audit_model.log_request(
                telegram_user_id=message.sender_id,
                source_message_id=source_id,
                outcome="denied",
                contact=contact,
                input_type=input_type,
            )
            reply = (
                "El acceso a este bot está bloqueado. Contacta a un administrador."
                if contact
                else "Envía un mensaje nuevo para iniciar el registro."
            )
            self._send_telegram_query_reply(
                client,
                message.chat_id,
                reply,
                message.message_id,
            )
            return
        if contact.onboarding_state != "complete":
            self._process_telegram_onboarding(client, message, contact)
            return

        params = self.env["ir.config_parameter"].sudo()
        if self._is_telegram_rate_limited(message.sender_id):
            audit_model.log_request(
                telegram_user_id=message.sender_id,
                source_message_id=source_id,
                outcome="rate_limited",
                contact=contact,
                input_type=input_type,
            )
            self._send_telegram_query_reply(
                client,
                message.chat_id,
                "Límite alcanzado: intenta de nuevo en unos minutos.",
                message.message_id,
            )
            return

        if voice_duration_seconds is not None and voice_duration_seconds > 60:
            audit_model.log_request(
                telegram_user_id=message.sender_id,
                source_message_id=source_id,
                outcome="invalid",
                contact=contact,
                input_type=input_type,
            )
            self._send_telegram_query_reply(
                client,
                message.chat_id,
                "La nota de voz supera el límite de 60 segundos. Envía una más corta.",
                message.message_id,
            )
            return

        question = message.text
        if voice_audio is not None:
            endpoint = os.environ.get("RIOCLARO_SPEECH_ENDPOINT", "")
            region = os.environ.get("RIOCLARO_SPEECH_REGION", "")
            api_key = os.environ.get("RIOCLARO_SPEECH_KEY", "")
            try:
                speech_client = AzureSpeechClient(
                    endpoint=endpoint,
                    region=region,
                    api_key=api_key,
                )
                question = speech_client.transcribe(voice_audio, language="es-MX")
            except (ValueError, SpeechError):
                audit_model.log_request(
                    telegram_user_id=message.sender_id,
                    source_message_id=source_id,
                    outcome="error",
                    contact=contact,
                    input_type=input_type,
                )
                self._send_telegram_query_reply(
                    client,
                    message.chat_id,
                    "No pude transcribir la nota de voz. Intenta enviarla de nuevo o escribe tu pregunta.",
                    message.message_id,
                )
                return

        if is_capability_help_request(question):
            result = {
                "ok": True,
                "tool": "help",
                "rows": [],
                "answer": self._telegram_help_response(contact),
            }
        else:
            result = self.env["rioclaro.assisted.query.ai"].answer(
                message.sender_id, question
            )
        outcome = {
            "denied": "denied",
            "company_forbidden": "denied",
            "company_required": "scope_required",
            "invalid_question": "invalid",
            "invalid_input": "invalid",
            "ai_not_configured": "error",
            "ai_unavailable": "error",
        }.get(result.get("error"), "allowed" if result.get("ok") else "error")
        tool = result.get("tool")
        audit_model.log_request(
            telegram_user_id=message.sender_id,
            source_message_id=source_id,
            outcome=outcome,
            contact=contact,
            tool=tool,
            input_type=input_type,
            company_ids=result.get("company_ids"),
        )
        if result.get("ok"):
            reply = str(result.get("answer", ""))[:_TELEGRAM_MAX_REPLY_LENGTH]
            speech_reply = reply
            parse_mode = None
            if result.get("tool") == "public_catalog":
                rows = result.get("rows", [])
                if rows:
                    try:
                        linked_reply = link_public_products(
                            reply,
                            rows,
                            base_url=params.search(
                                [("key", "=", "web.base.url")], limit=1
                            ).value
                            or "",
                            line_break="\n",
                            target_blank=False,
                        )
                    except ValueError:
                        _logger.warning(
                            "Product links for Telegram query %s could not be "
                            "built because web.base.url is invalid.",
                            message.message_id,
                        )
                    else:
                        if '<a href="' in linked_reply:
                            if len(linked_reply) <= _TELEGRAM_MAX_REPLY_LENGTH:
                                reply = str(linked_reply)
                                parse_mode = "HTML"
                            else:
                                _logger.warning(
                                    "Product links for Telegram query %s exceeded "
                                    "the reply length limit.",
                                    message.message_id,
                                )
            if voice_audio is not None:
                try:
                    spoken_reply = speech_client.synthesize(
                        speech_reply, language="es-MX"
                    )
                    client.send_voice(message.chat_id, spoken_reply)
                    self._send_telegram_query_reply(
                        client,
                        message.chat_id,
                        reply,
                        message.message_id,
                        parse_mode=parse_mode,
                    )
                    return
                except (ValueError, SpeechError, TelegramError):
                    _logger.warning(
                        "Voice response for Telegram query %s could not be delivered.",
                        message.message_id,
                    )
        elif result.get("error") in {"company_required", "company_forbidden"}:
            allowed_companies = result.get("allowed_companies", [])
            company_list = ", ".join(allowed_companies)
            if result.get("error") == "company_required":
                if company_list:
                    reply = (
                        "Para consultar datos internos, indica el nombre de una o "
                        f"más empresas autorizadas: {company_list}."
                    )
                else:
                    reply = (
                        "Tu cuenta no tiene empresas asignadas para consultas "
                        "privadas. Solicita acceso a un administrador."
                    )
            elif company_list:
                reply = (
                    "No tienes acceso a la empresa indicada. Puedes consultar: "
                    f"{company_list}."
                )
            else:
                reply = (
                    "No tienes acceso a la empresa indicada. Solicita acceso a un "
                    "administrador."
                )
        elif outcome == "denied":
            reply = "No tienes permiso para consultar esos datos."
        elif outcome == "invalid":
            reply = (
                "No pude interpretar esa consulta. "
                "Prueba con un periodo como 7, 30 o AAAA-MM-DD,AAAA-MM-DD."
            )
        else:
            reply = "El asistente no está disponible en este momento."
        self._send_telegram_query_reply(
            client,
            message.chat_id,
            reply,
            message.message_id,
            parse_mode=parse_mode if result.get("ok") else None,
        )

    @api.model
    def _send_telegram_query_reply(
        self,
        client: TelegramClient,
        chat_id: int,
        reply: str,
        message_id: int,
        *,
        parse_mode: Literal["HTML"] | None = None,
    ) -> None:
        """Send a query response and log delivery failures without rerunning AI."""
        try:
            if parse_mode:
                client.send_message(chat_id, reply, parse_mode=parse_mode)
            else:
                client.send_message(chat_id, reply)
        except TelegramError:
            _logger.warning(
                "Telegram response for text query %s could not be delivered.",
                message_id,
            )

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
    
    def _get_extractable_attachments(self):
        """Return PDF/JPEG/PNG attachments, PDFs first, then oldest first."""
        self.ensure_one()
        attachments = self.env["ir.attachment"].search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
        ]) | self.message_ids.attachment_ids
        candidates = attachments.filtered(
            lambda attachment: attachment.mimetype in _EXTRACTABLE_MIMETYPES
        )
        return candidates.sorted(
            lambda attachment: (attachment.mimetype != "application/pdf", attachment.id)
        )

    def _get_extractable_attachment(self):
        """Return the preferred PDF/JPEG/PNG attachment, or an empty recordset."""
        return self._get_extractable_attachments()[:1]

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
        attachments = self._get_extractable_attachments()
        if not attachments:
            raise UserError(_("No PDF, JPEG or PNG attachment found to extract."))

        client = AzureDocumentIntelligenceClient(endpoint, api_key)
        result = None
        try:
            # Emails often carry logos or signatures: keep the first attachment
            # that yields invoice data, otherwise the first result.
            for attachment in attachments:
                candidate = client.analyze_invoice(attachment.raw, attachment.mimetype)
                result = result or candidate
                if candidate.invoice_number or candidate.amount_total:
                    result = candidate
                    break
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
    
    def _get_purchase_tax_commands(self) -> list:
        """Return ORM commands linking the purchase tax implied by the extracted amounts.

        The rate is derived from amount_tax / amount_untaxed and matched against the
        company's percent-type purchase taxes. Returns an empty list when no tax was
        extracted or no tax matches.
        """
        self.ensure_one()
        if not (self.amount_tax and self.amount_untaxed):
            return []
        rate = round(self.amount_tax / self.amount_untaxed * 100, 2)
        tax = self.env["account.tax"].search(
            [
                ("type_tax_use", "=", "purchase"),
                ("amount_type", "=", "percent"),
                ("amount", "=", rate),
                ("company_id", "=", self.company_id.id),
            ],
            limit=1,
        )
        return [(6, 0, tax.ids)] if tax else []

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

        tax_ids = self._get_purchase_tax_commands()
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
                        "tax_ids": tax_ids,
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
                        "tax_ids": tax_ids,
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
