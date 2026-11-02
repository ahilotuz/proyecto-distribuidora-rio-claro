import html
import logging
import re
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from markupsafe import Markup

from odoo import _, fields, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)
_LIVECHAT_AI_QUERY_LIMIT = 5
_LIVECHAT_AI_WINDOW_MINUTES = 5


def _link_public_products(answer: str, rows: list[dict[str, Any]]) -> str:
    """Escape a public catalog answer and link exact published product names."""
    links: dict[str, str] = {}
    for row in rows:
        name = row.get("name")
        url = row.get("url")
        if not isinstance(name, str) or not name.strip() or not isinstance(url, str):
            continue
        parsed_url = urlsplit(url)
        if parsed_url.scheme not in {"", "http", "https"}:
            continue
        path = parsed_url.path
        if not path.startswith("/shop/") or path == "/shop/":
            continue
        escaped_name = html.escape(name)
        escaped_path = html.escape(path, quote=True)
        links[escaped_name] = (
            f'<a href="{escaped_path}" target="_blank" rel="noopener noreferrer">'
            f"{escaped_name}</a>"
        )

    safe_answer = html.escape(answer)
    if links:
        alternatives = "|".join(
            re.escape(name) for name in sorted(links, key=len, reverse=True)
        )
        product_pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")
        safe_answer = product_pattern.sub(
            lambda match: links[match.group(0)], safe_answer
        )
    return Markup(safe_answer.replace("\n", "<br/>"))


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
            and history.livechat_member_type == "bot"
            for history in channel.livechat_channel_member_history_ids
        ):
            return result
        if channel._livechat_agent_has_replied():
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
            if answer.get("tool") == "public_catalog":
                reply = _link_public_products(reply, answer.get("rows", []))
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

    def _livechat_agent_has_replied(self) -> bool:
        """Return whether a human agent wrote after the visitor's first message.

        Odoo assigns an operator when the chat starts and posts the welcome
        message as that operator, so only agent comments that follow the first
        visitor comment count as a real human reply.
        """
        agent_partners = self.livechat_channel_member_history_ids.filtered(
            lambda history: history.livechat_member_type == "agent"
        ).partner_id
        if not agent_partners:
            return False
        comments = self.env["mail.message"].sudo().search(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "=", self.id),
                ("message_type", "=", "comment"),
            ],
            order="id",
        )
        first_visitor_message = comments.filtered(
            lambda comment: comment.author_id not in agent_partners
            and comment.author_id != self.env.company.partner_id
        )[:1]
        if not first_visitor_message:
            return False
        return any(
            comment.id > first_visitor_message.id
            and comment.author_id in agent_partners
            for comment in comments
        )

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
