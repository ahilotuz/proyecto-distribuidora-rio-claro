from unittest.mock import patch

from odoo.addons.rioclaro_invoice_inbox.models.assisted_query_ai import AssistedQueryAI
from odoo.tests import TransactionCase, tagged
from odoo.tools import html2plaintext


@tagged("post_install", "-at_install")
class TestWebsiteLivechatAI(TransactionCase):
    def _visitor_channel(self, enabled=True):
        livechat_type = self.env["im_livechat.channel"].create(
            {"name": "Río Claro AI test", "rioclaro_ai_enabled": enabled}
        )
        guest = self.env["mail.guest"].create({"name": "Website visitor"})
        channel = self.env["discuss.channel"].create(
            {
                "name": "Public AI test",
                "channel_type": "livechat",
                "livechat_channel_id": livechat_type.id,
            }
        )
        member = channel._add_members(
            guests=guest,
            post_joined_message=False,
        )
        member.livechat_member_type = "visitor"
        (channel.channel_member_ids - member).unlink()
        return channel, guest

    def test_public_ai_replies_only_on_opted_in_livechat_channel(self):
        channel, guest = self._visitor_channel()
        visitor_message = self.env["mail.message"].create(
            {
                "model": "discuss.channel",
                "res_id": channel.id,
                "body": "<p>¿Qué productos tienen?</p>",
                "message_type": "comment",
                "author_guest_id": guest.id,
            }
        )

        with patch.object(
            AssistedQueryAI,
            "answer_public",
            return_value={
                "ok": True,
                "tool": "public_catalog",
                "answer": "Tenemos productos publicados en el sitio.",
            },
        ) as answer_public:
            channel._message_post_after_hook(visitor_message)

        answer_public.assert_called_once_with("¿Qué productos tienen?")
        self.assertTrue(
            any(
                "Tenemos productos publicados en el sitio."
                in html2plaintext(body)
                for body in channel.message_ids.mapped("body")
            )
        )

    def test_public_ai_rate_limits_guest_without_calling_model(self):
        channel, guest = self._visitor_channel()
        for index in range(5):
            self.env["mail.message"].create(
                {
                    "model": "discuss.channel",
                    "res_id": channel.id,
                    "body": f"<p>Previous question {index}</p>",
                    "message_type": "comment",
                    "author_guest_id": guest.id,
                }
            )
        visitor_message = self.env["mail.message"].create(
            {
                "model": "discuss.channel",
                "res_id": channel.id,
                "body": "<p>Una pregunta más</p>",
                "message_type": "comment",
                "author_guest_id": guest.id,
            }
        )

        with patch.object(AssistedQueryAI, "answer_public") as answer_public:
            channel._message_post_after_hook(visitor_message)

        answer_public.assert_not_called()
        self.assertTrue(
            any(
                "Espera unos minutos"
                in html2plaintext(body)
                for body in channel.message_ids.mapped("body")
            )
        )

    def test_public_ai_is_off_by_default(self):
        channel = self.env["im_livechat.channel"].create(
            {"name": "Río Claro AI disabled test"}
        )
        self.assertFalse(channel.rioclaro_ai_enabled)

    def test_public_ai_does_not_answer_when_channel_is_not_opted_in(self):
        channel, guest = self._visitor_channel(enabled=False)
        visitor_message = self.env["mail.message"].create(
            {
                "model": "discuss.channel",
                "res_id": channel.id,
                "body": "<p>¿Qué productos tienen?</p>",
                "message_type": "comment",
                "author_guest_id": guest.id,
            }
        )

        with patch.object(AssistedQueryAI, "answer_public") as answer_public:
            channel._message_post_after_hook(visitor_message)

        answer_public.assert_not_called()
