import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.exceptions import AzureError


class AccessToken(Protocol):
    token: str


class TokenCredential(Protocol):
    def get_token(self, *scopes: str) -> AccessToken: ...


class ScreeningConfigurationError(RuntimeError):
    pass


class ScreeningServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Classification:
    label: str
    confidence: float


@dataclass(frozen=True)
class ScreeningDecision:
    route: str
    label: str
    confidence: float


def decide_route(
    classification: Classification,
    has_attachments: bool,
    confidence_threshold: float,
) -> ScreeningDecision:
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("The confidence threshold must be between 0 and 1.")
    if not 0.0 <= classification.confidence <= 1.0:
        raise ValueError("The classification confidence must be between 0 and 1.")

    if classification.confidence < confidence_threshold:
        route = "review"
    elif classification.label == "not_invoice":
        route = "ignore"
    elif classification.label == "supplier_invoice" and has_attachments:
        route = "inspect_attachments"
    else:
        route = "review"

    return ScreeningDecision(
        route=route,
        label=classification.label,
        confidence=classification.confidence,
    )


class AzureLanguageClassifier:
    def __init__(
        self,
        endpoint: str,
        project_name: str,
        deployment_name: str,
        credential: TokenCredential,
        timeout_seconds: int = 60,
    ) -> None:
        if not endpoint or not project_name or not deployment_name:
            raise ScreeningConfigurationError(
                "Configure LANGUAGE_ENDPOINT, LANGUAGE_PROJECT_NAME, "
                "and LANGUAGE_DEPLOYMENT_NAME."
            )
        self.endpoint = endpoint.rstrip("/")
        self.project_name = project_name
        self.deployment_name = deployment_name
        self.credential = credential
        self.timeout_seconds = timeout_seconds
        self.client = TextAnalyticsClient(
            endpoint=self.endpoint,
            credential=self.credential,
        )

    def classify(self, body: str, language: str) -> Classification:
        try:
            poller = self.client.begin_single_label_classify(
                documents=[
                    {
                        "id": "email-body",
                        "text": body,
                        "language": language,
                    }
                ],
                project_name=self.project_name,
                deployment_name=self.deployment_name,
                polling_interval=1,
            )
            results = poller.result(timeout=self.timeout_seconds)
        except (AzureError, TimeoutError) as error:
            raise ScreeningServiceError(
                "Azure AI Language classification request failed."
            ) from error

        for result in results:
            if result.is_error:
                raise ScreeningServiceError(
                    "Azure AI Language could not classify the email body."
                )
            if result.id != "email-body":
                continue
            if result.kind != "CustomDocumentClassification":
                raise ScreeningServiceError(
                    "Azure AI Language returned an unexpected result type."
                )
            if not result.classifications:
                raise ScreeningServiceError(
                    "Azure AI Language returned no classification."
                )

            prediction = max(
                result.classifications,
                key=lambda classification: classification.confidence_score,
            )
            return Classification(
                label=prediction.category,
                confidence=float(prediction.confidence_score),
            )

        raise ScreeningServiceError(
            "Azure AI Language response did not contain the requested document."
        )


@lru_cache(maxsize=1)
def build_classifier() -> AzureLanguageClassifier:
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as error:
        raise ScreeningConfigurationError(
            "Install the Azure Function dependencies before starting the service."
        ) from error

    endpoint = os.environ.get("LANGUAGE_ENDPOINT", "")
    project_name = os.environ.get("LANGUAGE_PROJECT_NAME", "")
    deployment_name = os.environ.get("LANGUAGE_DEPLOYMENT_NAME", "")
    return AzureLanguageClassifier(
        endpoint=endpoint,
        project_name=project_name,
        deployment_name=deployment_name,
        credential=DefaultAzureCredential(),
    )


def read_confidence_threshold() -> float:
    raw_threshold = os.environ.get("INVOICE_CONFIDENCE_THRESHOLD", "0.90")
    try:
        threshold = float(raw_threshold)
    except ValueError as error:
        raise ScreeningConfigurationError(
            "INVOICE_CONFIDENCE_THRESHOLD must be a number between 0 and 1."
        ) from error
    if not 0.0 <= threshold <= 1.0:
        raise ScreeningConfigurationError(
            "INVOICE_CONFIDENCE_THRESHOLD must be a number between 0 and 1."
        )
    return threshold
