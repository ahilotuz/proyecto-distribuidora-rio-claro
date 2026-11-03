"""Tests for Azure OpenAI assisted query tool-picking."""

import json
from unittest.mock import Mock, patch

from odoo.addons.rioclaro_invoice_inbox.models.assisted_query_ai import (
    AzureOpenAIClient,
    AzureOpenAIError,
)
from odoo.addons.rioclaro_invoice_inbox.models.query_service import QueryService
from odoo.tests import TransactionCase, tagged

_URLOPEN = "odoo.addons.rioclaro_invoice_inbox.models.assisted_query_ai.urlopen"


def _response(body: bytes) -> Mock:
    response = Mock()
    response.read.return_value = body
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


def _chat_message(payload: dict) -> Mock:
    return _response(json.dumps({"choices": [{"message": payload}]}).encode())


@tagged("post_install", "-at_install")
class TestAzureOpenAIClient(TransactionCase):
    @patch(_URLOPEN)
    def test_choose_tool_parses_function_call(self, urlopen_mock):
        urlopen_mock.return_value = _chat_message(
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "recent_sales",
                            "arguments": '{"text":"S00027"}',
                        }
                    }
                ]
            }
        )
        client = AzureOpenAIClient(
            endpoint="https://example.openai.azure.com",
            api_key="key",
            deployment="gpt-4o-mini",
        )

        choice = client.choose_tool("¿Cuál es el estado de S00027?")

        self.assertEqual(choice.tool, "recent_sales")
        self.assertEqual(choice.text, "S00027")
        payload = json.loads(urlopen_mock.call_args.args[0].data)
        self.assertEqual(payload["max_completion_tokens"], 400)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("temperature", payload)

    @patch(_URLOPEN)
    def test_choose_tool_rejects_unknown_tool(self, urlopen_mock):
        urlopen_mock.return_value = _chat_message(
            {
                "tool_calls": [
                    {"function": {"name": "drop_tables", "arguments": '{"text":""}'}}
                ]
            }
        )
        client = AzureOpenAIClient(
            endpoint="https://example.openai.azure.com",
            api_key="key",
            deployment="gpt-4o-mini",
        )

        with self.assertRaises(AzureOpenAIError):
            client.choose_tool("hola")

    @patch(_URLOPEN)
    def test_summarize_rows_strips_markdown(self, urlopen_mock):
        urlopen_mock.return_value = _chat_message(
            {"content": "- **Orden S00027**: `Confirmada`\n* Total __$10__"}
        )
        client = AzureOpenAIClient(
            endpoint="https://example.openai.azure.com",
            api_key="key",
            deployment="gpt-4o-mini",
        )

        text = client.summarize_rows(question="estado", tool="order_status", rows=[])

        self.assertEqual(text, "• Orden S00027: Confirmada\n• Total $10")

    @patch(_URLOPEN)
    def test_summarize_rows_returns_content(self, urlopen_mock):
        urlopen_mock.return_value = _chat_message(
            {"content": "• Orden S00027: Confirmada."}
        )
        client = AzureOpenAIClient(
            endpoint="https://example.openai.azure.com",
            api_key="key",
            deployment="gpt-4o-mini",
        )

        text = client.summarize_rows(
            question="estado",
            tool="order_status",
            rows=[{"reference": "S00027", "state": "sale"}],
        )

        self.assertIn("S00027", text)


@tagged("post_install", "-at_install")
class TestAssistedQueryAI(TransactionCase):
    def setUp(self):
        super().setUp()
        self.ai = self.env["rioclaro.assisted.query.ai"]
        self.env["rioclaro.telegram.contact"].create(
            {"name": "Internal test contact", "telegram_user_id": "1", "role": "internal"}
        )

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_env_returns_not_configured(self):
        self.assertEqual(
            self.ai.answer(1, "ventas"),
            {"ok": False, "error": "ai_not_configured"},
        )

    @patch.dict(
        "os.environ",
        {
            "RIOCLARO_AOAI_ENDPOINT": "https://example.openai.azure.com",
            "RIOCLARO_AOAI_API_KEY": "key",
            "RIOCLARO_AOAI_DEPLOYMENT": "gpt-4o-mini",
        },
    )
    @patch.object(AzureOpenAIClient, "summarize_rows")
    @patch.object(AzureOpenAIClient, "choose_tool")
    @patch.object(QueryService, "run_tool")
    def test_answer_happy_path(
        self,
        run_tool_mock,
        choose_tool_mock,
        summarize_mock,
    ):
        choose_tool_mock.return_value = type("Choice", (), {"tool": "recent_sales", "text": ""})()
        run_tool_mock.return_value = {
            "ok": True,
            "tool": "recent_sales",
            "rows": [{"reference": "S00027"}],
            "company_ids": [self.env.company.id],
        }
        summarize_mock.return_value = "• S00027"

        result = self.ai.answer(
            1, f"ventas recientes en {self.env.company.name}"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "recent_sales")
        run_tool_mock.assert_called_once_with(
            1,
            "recent_sales",
            "",
            company_ids=[self.env.company.id],
        )

    @patch.dict(
        "os.environ",
        {
            "RIOCLARO_AOAI_ENDPOINT": "https://example.openai.azure.com",
            "RIOCLARO_AOAI_API_KEY": "key",
            "RIOCLARO_AOAI_DEPLOYMENT": "gpt-4o-mini",
        },
    )
    @patch.object(AzureOpenAIClient, "choose_tool")
    @patch.object(QueryService, "run_tool")
    def test_answer_returns_denied_from_query_service(self, run_tool_mock, choose_tool_mock):
        choose_tool_mock.return_value = type("Choice", (), {"tool": "recent_sales", "text": ""})()
        run_tool_mock.return_value = {"ok": False, "error": "denied"}

        result = self.ai.answer(1, f"ventas en {self.env.company.name}")

        self.assertEqual(result, {"ok": False, "error": "denied", "tool": "recent_sales"})
