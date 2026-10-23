"""Azure OpenAI tool-picker over the fixed read-only query service tools."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from odoo import api, models

from .query_service import TOOL_REGISTRY

_CHAT_API_VERSION = "2024-06-01"
_MAX_QUESTION_LENGTH = 280


class AzureOpenAIError(Exception):
    """Raised when Azure OpenAI returns an invalid or unsuccessful response."""


@dataclass(frozen=True)
class ToolChoice:
    """A tool selected by the model and the text argument passed to it."""

    tool: str
    text: str


class AzureOpenAIClient:
    """Small Azure OpenAI chat client with enforced tool-calling."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        deployment: str,
        request_timeout: float = 35.0,
    ) -> None:
        parsed_endpoint = urlsplit(endpoint.strip())
        if (
            parsed_endpoint.scheme != "https"
            or not parsed_endpoint.hostname
            or parsed_endpoint.username
            or parsed_endpoint.password
            or parsed_endpoint.query
            or parsed_endpoint.fragment
        ):
            raise ValueError("Azure OpenAI endpoint must be an HTTPS URL.")
        if not api_key.strip():
            raise ValueError("Azure OpenAI API key must not be empty.")
        if not deployment.strip():
            raise ValueError("Azure OpenAI deployment must not be empty.")
        if request_timeout <= 0:
            raise ValueError("Azure OpenAI timeout must be positive.")

        self._endpoint = endpoint.strip().rstrip("/")
        self._api_key = api_key.strip()
        self._deployment = deployment.strip()
        self._request_timeout = request_timeout

    def choose_tool(self, question: str) -> ToolChoice:
        """Select exactly one fixed tool and a short text argument."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": self._tool_description(name),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "description": (
                                    "Optional search text (product, order reference, or "
                                    "partner hint). Empty when unnecessary."
                                ),
                            }
                        },
                        "additionalProperties": False,
                    },
                },
            }
            for name in TOOL_REGISTRY
        ]
        message = self._chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict dispatcher. Choose exactly one function from the "
                        "provided list. Never answer with business data directly."
                    ),
                },
                {"role": "user", "content": question},
            ],
            tools=tools,
            tool_choice="required",
        )
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            raise AzureOpenAIError("Azure OpenAI did not return a function call.")
        function = tool_calls[0].get("function") if isinstance(tool_calls[0], dict) else None
        if not isinstance(function, dict):
            raise AzureOpenAIError("Azure OpenAI returned an invalid function call.")
        tool_name = function.get("name")
        if not isinstance(tool_name, str) or tool_name not in TOOL_REGISTRY:
            raise AzureOpenAIError("Azure OpenAI selected an unknown tool.")
        raw_arguments = function.get("arguments")
        if not isinstance(raw_arguments, str):
            raise AzureOpenAIError("Azure OpenAI returned invalid function arguments.")
        try:
            parsed_arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as error:
            raise AzureOpenAIError("Azure OpenAI returned malformed function arguments.") from error
        text = parsed_arguments.get("text", "")
        if not isinstance(text, str):
            raise AzureOpenAIError("Azure OpenAI returned a non-text argument.")
        return ToolChoice(tool=tool_name, text=text.strip())

    def summarize_rows(self, *, question: str, tool: str, rows: list[dict[str, Any]]) -> str:
        """Generate a short Spanish answer from already-authorized rows only."""
        message = self._chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Responde en español, con máximo 4 viñetas. "
                        "Usa solo la información del bloque DATA. "
                        "Si DATA está vacío, di que no hay resultados."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"QUESTION: {question}\n"
                        f"TOOL: {tool}\n"
                        f"DATA: {json.dumps(rows, ensure_ascii=False)}"
                    ),
                },
            ]
        )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AzureOpenAIError("Azure OpenAI did not return a text answer.")
        return content.strip()

    def _chat(
        self,
        *,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> dict[str, Any]:
        path = (
            f"/openai/deployments/{quote(self._deployment)}/chat/completions"
            f"?api-version={_CHAT_API_VERSION}"
        )
        payload: dict[str, Any] = {
            "messages": messages,
            "max_completion_tokens": 400,
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        request = Request(
            f"{self._endpoint}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "api-key": self._api_key},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._request_timeout) as response:
                parsed = json.loads(response.read())
        except HTTPError as error:
            raise AzureOpenAIError(
                f"Azure OpenAI chat completion returned HTTP {error.code}."
            ) from error
        except (URLError, TimeoutError) as error:
            raise AzureOpenAIError("Azure OpenAI chat completion could not be reached.") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise AzureOpenAIError("Azure OpenAI chat completion returned invalid JSON.") from error

        choices = parsed.get("choices") if isinstance(parsed, dict) else None
        if not isinstance(choices, list) or not choices:
            raise AzureOpenAIError("Azure OpenAI chat completion returned no choices.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise AzureOpenAIError("Azure OpenAI chat completion returned no message.")
        return message

    @staticmethod
    def _tool_description(tool_name: str) -> str:
        descriptions = {
            "stock_by_product": "Consulta inventario por producto (existencia y disponible).",
            "search_products": "Busca productos candidatos por nombre o SKU (sin cantidades).",
            "get_product_stock": "Existencias de un producto por ID numérico obtenido con search_products, opcional ',almacen_id'.",
            "recent_purchases": "Devuelve compras recientes autorizadas para el remitente.",
            "recent_sales": "Devuelve ventas recientes autorizadas para el remitente.",
            "recent_invoices": "Devuelve facturas publicadas autorizadas para el remitente.",
            "aggregate_sales": "Total de ventas confirmadas por moneda. Texto: vacío (30 días), número de días (1-366) o 'AAAA-MM-DD,AAAA-MM-DD'.",
            "aggregate_purchases": "Total de compras confirmadas por moneda. Texto: vacío (30 días), número de días (1-366) o 'AAAA-MM-DD,AAAA-MM-DD'.",
            "aggregate_invoices": "Total de facturas publicadas (notas de crédito restadas) por tipo y moneda. Texto: vacío (30 días), número de días (1-366) o 'AAAA-MM-DD,AAAA-MM-DD'.",
            "order_status": "Busca el estado de una orden por referencia exacta.",
        }
        return descriptions[tool_name]


class AssistedQueryAI(models.AbstractModel):
    """Selects a read-only tool and phrases the answer through Azure OpenAI."""

    _name = "rioclaro.assisted.query.ai"
    _description = "Assisted Query AI"

    @api.model
    def answer(self, telegram_user_id: int | str, question: str) -> dict[str, Any]:
        """Answer one text question by selecting exactly one fixed query tool."""
        clean_question = (question or "").strip()
        if not clean_question or len(clean_question) > _MAX_QUESTION_LENGTH:
            return {"ok": False, "error": "invalid_question"}

        endpoint = os.environ.get("RIOCLARO_AOAI_ENDPOINT", "")
        api_key = os.environ.get("RIOCLARO_AOAI_API_KEY", "")
        deployment = os.environ.get("RIOCLARO_AOAI_DEPLOYMENT", "")
        if not endpoint or not api_key or not deployment:
            return {"ok": False, "error": "ai_not_configured"}

        try:
            client = AzureOpenAIClient(
                endpoint=endpoint,
                api_key=api_key,
                deployment=deployment,
            )
            choice = client.choose_tool(clean_question)
        except (ValueError, AzureOpenAIError):
            return {"ok": False, "error": "ai_unavailable"}

        result = self.env["rioclaro.query.service"].run_tool(
            telegram_user_id, choice.tool, choice.text
        )
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error", "query_failed"), "tool": choice.tool}

        try:
            answer_text = client.summarize_rows(
                question=clean_question,
                tool=choice.tool,
                rows=result["rows"],
            )
        except AzureOpenAIError:
            return {"ok": False, "error": "ai_unavailable", "tool": choice.tool}

        return {
            "ok": True,
            "tool": choice.tool,
            "rows": result["rows"],
            "answer": answer_text,
        }
