import logging
from datetime import timedelta
from typing import Any

from odoo import _, fields, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)
_LIVECHAT_AI_QUERY_LIMIT = 5
_LIVECHAT_AI_WINDOW_MINUTES = 5


class LivechatChannel(models.Model):
    """Allow managers to opt a Live Chat channel into public-content AI replies."""

    _inherit = "im_livechat.channel"

    rioclaro_ai_enabled = fields.Boolean(
        string="Río Claro AI assistant",
        help=(
            "Answer visitor questions using published website products and pages "
            "only. This assistant cannot access prices, stock, orders, invoices, "
            "or customer records."
        ),
    )


class DiscussChannel(models.Model):
    """Connect opted-in website Live Chat conversations to the public AI agent."""

    _inherit = "discuss.channel"

    def _message_post_after_hook(self, message: models.Model) -> Any:
        result = super()._message_post_after_hook(message)
        if (
            self.channel_type != "livechat"
            or not self.livechat_channel_id.rioclaro_ai_enabled
            or message.message_type != "comment"
            or self.chatbot_current_step_id
        ):
            return result

        channel = self.sudo()
        active_member_ids = set(channel.channel_member_ids.ids)
        if any(
            history.member_id.id in active_member_ids
            and history.livechat_member_type in ("agent", "bot")
            for history in channel.livechat_channel_member_history_ids
        ):
            return result

        author_history = channel.livechat_channel_member_history_ids.filtered(
            lambda history: (
                history.guest_id == message.author_guest_id
                if message.author_guest_id
                else history.partner_id == message.author_id
            )
        )
        if not author_history or author_history[-1].livechat_member_type != "visitor":
            return result

        question = html2plaintext(message.body or "").strip()
        if not question:
            return result

        if self._is_livechat_ai_rate_limited(message):
            reply = _("Espera unos minutos antes de volver a consultar.")
            self.sudo().message_post(
                body=reply,
                author_id=self.env.company.partner_id.id,
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
            return result

        answer = self.env["rioclaro.assisted.query.ai"].answer_public(question)
        if answer.get("ok"):
            reply = answer["answer"]
        else:
            _logger.warning(
                "Public Live Chat AI failed for channel %s, message %s: %s",
                self.id,
                message.id,
                answer.get("error", "unknown error"),
            )
            reply = _(
                "No puedo responder en este momento. Deja tu mensaje y un agente "
                "te ayudará."
            )

        self.sudo().message_post(
            body=reply,
            author_id=self.env.company.partner_id.id,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        return result

    def _is_livechat_ai_rate_limited(self, message: models.Model) -> bool:
        """Allow at most five visitor questions in a rolling five-minute window."""
        identity_domain = (
            [("author_guest_id", "=", message.author_guest_id.id)]
            if message.author_guest_id
            else [("author_id", "=", message.author_id.id)]
        )
        livechat_channel_ids = self.env["discuss.channel"].sudo().search(
            [("channel_type", "=", "livechat")]
        ).ids
        recent_count = self.env["mail.message"].sudo().search_count(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "in", livechat_channel_ids),
                ("message_type", "=", "comment"),
                ("date", ">=", fields.Datetime.now() - timedelta(
                    minutes=_LIVECHAT_AI_WINDOW_MINUTES
                )),
            ]
            + identity_domain
        )
        return recent_count > _LIVECHAT_AI_QUERY_LIMIT
