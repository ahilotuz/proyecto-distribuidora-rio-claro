"""Azure Document Intelligence client and invoice-result parsing."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


_API_VERSION = "2024-11-30"
_ANALYZE_PATH = (
    "/documentintelligence/documentModels/prebuilt-invoice:analyze"
)
_SUPPORTED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
}


class DocumentIntelligenceError(Exception):
    """Raised when Azure invoice analysis fails or returns invalid data."""


@dataclass(frozen=True)
class ExtractedInvoiceLine:
    """A line item extracted from a supplier invoice."""

    description: str | None
    quantity: float | None
    unit_price: float | None
    amount: float | None
    confidence: float | None


@dataclass(frozen=True)
class InvoiceExtraction:
    """Invoice fields and confidence scores returned by Azure."""

    supplier_rfc: str | None
    supplier_name: str | None
    invoice_number: str | None
    invoice_date: str | None
    currency_code: str | None
    amount_untaxed: float | None
    amount_tax: float | None
    amount_total: float | None
    confidence_scores: dict[str, float]
    lines: tuple[ExtractedInvoiceLine, ...]


class AzureDocumentIntelligenceClient:
    """Submit documents to Azure's prebuilt-invoice model and parse results."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        request_timeout: float = 35.0,
        poll_timeout: float = 120.0,
        poll_interval: float = 1.0,
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
            raise ValueError("Document Intelligence endpoint must be an HTTPS URL.")
        if not api_key.strip():
            raise ValueError("Document Intelligence API key must not be empty.")
        if request_timeout <= 0 or poll_timeout <= 0 or poll_interval < 0:
            raise ValueError("Document Intelligence timeouts must be positive.")

        self._endpoint = endpoint.strip().rstrip("/")
        self._host = parsed_endpoint.hostname.lower()
        self._api_key = api_key.strip()
        self._request_timeout = request_timeout
        self._poll_timeout = poll_timeout
        self._poll_interval = poll_interval

    def analyze_invoice(
        self,
        content: bytes,
        content_type: str,
    ) -> InvoiceExtraction:
        """Analyze one PDF or image and return normalized invoice fields."""
        if not content:
            raise ValueError("Invoice document content must not be empty.")
        normalized_content_type = content_type.split(";", maxsplit=1)[0].strip().lower()
        if normalized_content_type not in _SUPPORTED_CONTENT_TYPES:
            raise ValueError("Document must be a PDF, JPEG, or PNG image.")

        request = Request(
            f"{self._endpoint}{_ANALYZE_PATH}?api-version={_API_VERSION}",
            data=content,
            headers={
                "Content-Type": normalized_content_type,
                "Ocp-Apim-Subscription-Key": self._api_key,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._request_timeout) as response:
                operation_url = response.headers.get("operation-location", "")
        except HTTPError as error:
            raise DocumentIntelligenceError(
                f"Azure Document Intelligence returned HTTP {error.code}."
            ) from error
        except (URLError, TimeoutError) as error:
            raise DocumentIntelligenceError(
                "Azure Document Intelligence could not be reached."
            ) from error

        self._validate_operation_url(operation_url)
        result = self._poll_operation(operation_url)
        return self._parse_invoice_result(result)

    def _validate_operation_url(self, operation_url: str) -> None:
        parsed_url = urlsplit(operation_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.hostname.lower() != self._host
            or parsed_url.username
            or parsed_url.password
            or parsed_url.fragment
        ):
            raise DocumentIntelligenceError(
                "Azure Document Intelligence returned an invalid operation URL."
            )

    def _poll_operation(self, operation_url: str) -> dict[str, Any]:
        deadline = time.monotonic() + self._poll_timeout
        while time.monotonic() < deadline:
            request = Request(
                operation_url,
                headers={"Ocp-Apim-Subscription-Key": self._api_key},
                method="GET",
            )
            try:
                with urlopen(request, timeout=self._request_timeout) as response:
                    result = json.loads(response.read())
            except HTTPError as error:
                raise DocumentIntelligenceError(
                    f"Azure Document Intelligence polling returned HTTP {error.code}."
                ) from error
            except (URLError, TimeoutError) as error:
                raise DocumentIntelligenceError(
                    "Azure Document Intelligence polling could not be reached."
                ) from error
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise DocumentIntelligenceError(
                    "Azure Document Intelligence returned invalid JSON."
                ) from error

            if not isinstance(result, dict):
                raise DocumentIntelligenceError(
                    "Azure Document Intelligence returned an invalid operation result."
                )
            status = result.get("status")
            if status == "succeeded":
                return result
            if status == "failed":
                raise DocumentIntelligenceError(
                    "Azure Document Intelligence could not analyze the invoice."
                )
            if status not in {"notStarted", "running"}:
                raise DocumentIntelligenceError(
                    "Azure Document Intelligence returned an unknown operation status."
                )

            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(self._poll_interval, remaining))

        raise DocumentIntelligenceError(
            "Azure Document Intelligence analysis timed out."
        )

    @classmethod
    def _parse_invoice_result(cls, result: dict[str, Any]) -> InvoiceExtraction:
        analyze_result = result.get("analyzeResult")
        documents = (
            analyze_result.get("documents")
            if isinstance(analyze_result, dict)
            else None
        )
        if not isinstance(documents, list) or not documents:
            raise DocumentIntelligenceError(
                "Azure Document Intelligence found no invoice document."
            )
        document = documents[0]
        fields = document.get("fields") if isinstance(document, dict) else None
        if not isinstance(fields, dict):
            raise DocumentIntelligenceError(
                "Azure Document Intelligence returned no invoice fields."
            )

        confidence_scores: dict[str, float] = {}
        field_values: dict[str, Any] = {}
        for target_name, azure_name in (
            ("supplier_rfc", "VendorTaxId"),
            ("supplier_name", "VendorName"),
            ("invoice_number", "InvoiceId"),
            ("invoice_date", "InvoiceDate"),
            ("amount_untaxed", "SubTotal"),
            ("amount_tax", "TotalTax"),
            ("amount_total", "InvoiceTotal"),
        ):
            field = fields.get(azure_name)
            field_values[target_name] = field
            confidence = cls._confidence(field)
            if confidence is not None:
                confidence_scores[target_name] = confidence

        invoice_total = field_values["amount_total"]
        return InvoiceExtraction(
            supplier_rfc=cls._string_value(field_values["supplier_rfc"]),
            supplier_name=cls._string_value(field_values["supplier_name"]),
            invoice_number=cls._string_value(field_values["invoice_number"]),
            invoice_date=cls._string_value(field_values["invoice_date"]),
            currency_code=cls._currency_code(invoice_total),
            amount_untaxed=cls._number_value(field_values["amount_untaxed"]),
            amount_tax=cls._number_value(field_values["amount_tax"]),
            amount_total=cls._number_value(invoice_total),
            confidence_scores=confidence_scores,
            lines=cls._parse_lines(fields.get("Items")),
        )

    @staticmethod
    def _field(field: Any) -> dict[str, Any]:
        return field if isinstance(field, dict) else {}

    @classmethod
    def _string_value(cls, field: Any) -> str | None:
        values = cls._field(field)
        value = values.get("valueString") or values.get("valueDate")
        return value.strip() if isinstance(value, str) and value.strip() else None

    @classmethod
    def _number_value(cls, field: Any) -> float | None:
        values = cls._field(field)
        currency = values.get("valueCurrency")
        value = currency.get("amount") if isinstance(currency, dict) else None
        if value is None:
            value = values.get("valueNumber")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    @classmethod
    def _currency_code(cls, field: Any) -> str | None:
        currency = cls._field(field).get("valueCurrency")
        if not isinstance(currency, dict):
            return None
        code = currency.get("currencyCode")
        return code.strip() if isinstance(code, str) and code.strip() else None

    @classmethod
    def _confidence(cls, field: Any) -> float | None:
        confidence = cls._field(field).get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            return None
        return float(confidence)

    @classmethod
    def _parse_lines(cls, field: Any) -> tuple[ExtractedInvoiceLine, ...]:
        values = cls._field(field).get("valueArray")
        if values is None:
            return ()
        if not isinstance(values, list):
            raise DocumentIntelligenceError(
                "Azure Document Intelligence returned invalid invoice line items."
            )

        lines: list[ExtractedInvoiceLine] = []
        for item in values:
            item_fields = cls._field(cls._field(item).get("valueObject"))
            if not item_fields:
                raise DocumentIntelligenceError(
                    "Azure Document Intelligence returned an invalid invoice line."
                )
            lines.append(
                ExtractedInvoiceLine(
                    description=cls._string_value(item_fields.get("Description")),
                    quantity=cls._number_value(item_fields.get("Quantity")),
                    unit_price=cls._number_value(item_fields.get("UnitPrice")),
                    amount=cls._number_value(item_fields.get("Amount")),
                    confidence=cls._confidence(item),
                )
            )
        return tuple(lines)
