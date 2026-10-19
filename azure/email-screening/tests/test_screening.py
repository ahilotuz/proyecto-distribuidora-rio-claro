import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from screening import (
    AzureLanguageClassifier,
    Classification,
    ScreeningServiceError,
    decide_route,
)


class TestDecideRoute(unittest.TestCase):
    def test_confident_invoice_with_attachments_requires_attachment_inspection(self):
        decision = decide_route(
            Classification("supplier_invoice", 0.97),
            has_attachments=True,
            confidence_threshold=0.90,
        )

        self.assertEqual(decision.route, "inspect_attachments")

    def test_confident_invoice_without_attachments_goes_to_review(self):
        decision = decide_route(
            Classification("supplier_invoice", 0.97),
            has_attachments=False,
            confidence_threshold=0.90,
        )

        self.assertEqual(decision.route, "review")

    def test_uncertain_message_goes_to_review(self):
        decision = decide_route(
            Classification("not_invoice", 0.60),
            has_attachments=True,
            confidence_threshold=0.90,
        )

        self.assertEqual(decision.route, "review")

    def test_confident_non_invoice_is_ignored(self):
        decision = decide_route(
            Classification("not_invoice", 0.99),
            has_attachments=True,
            confidence_threshold=0.90,
        )

        self.assertEqual(decision.route, "ignore")

    def test_unknown_label_goes_to_review(self):
        decision = decide_route(
            Classification("other", 0.99),
            has_attachments=True,
            confidence_threshold=0.90,
        )

        self.assertEqual(decision.route, "review")

    def test_invalid_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            decide_route(
                Classification("supplier_invoice", 0.99),
                has_attachments=True,
                confidence_threshold=1.1,
            )


class TestAzureLanguageClassifier(unittest.TestCase):
    @patch("screening.TextAnalyticsClient")
    def test_sends_document_to_custom_classifier_and_parses_result(
        self,
        client_type,
    ):
        credential = Mock()
        client = client_type.return_value
        client.begin_single_label_classify.return_value.result.return_value = [
            SimpleNamespace(
                id="email-body",
                is_error=False,
                kind="CustomDocumentClassification",
                classifications=[
                    SimpleNamespace(
                        category="supplier_invoice",
                        confidence_score=0.95,
                    )
                ],
            )
        ]
        classifier = AzureLanguageClassifier(
            endpoint="https://language.example.test",
            project_name="invoice-email",
            deployment_name="production",
            credential=credential,
        )

        classification = classifier.classify("Factura adjunta", "es")

        self.assertEqual(classification, Classification("supplier_invoice", 0.95))
        client_type.assert_called_once_with(
            endpoint="https://language.example.test",
            credential=credential,
        )
        client.begin_single_label_classify.assert_called_once_with(
            documents=[
                {
                    "id": "email-body",
                    "text": "Factura adjunta",
                    "language": "es",
                }
            ],
            project_name="invoice-email",
            deployment_name="production",
            polling_interval=1,
        )

    @patch("screening.TextAnalyticsClient")
    def test_document_error_raises_service_error(self, client_type):
        client = client_type.return_value
        client.begin_single_label_classify.return_value.result.return_value = [
            SimpleNamespace(id="email-body", is_error=True)
        ]
        classifier = AzureLanguageClassifier(
            endpoint="https://language.example.test",
            project_name="invoice-email",
            deployment_name="production",
            credential=Mock(),
        )

        with self.assertRaises(ScreeningServiceError):
            classifier.classify("Factura adjunta", "es")

    @patch("screening.TextAnalyticsClient")
    def test_missing_prediction_raises_service_error(self, client_type):
        client = client_type.return_value
        client.begin_single_label_classify.return_value.result.return_value = []
        classifier = AzureLanguageClassifier(
            endpoint="https://language.example.test",
            project_name="invoice-email",
            deployment_name="production",
            credential=Mock(),
        )

        with self.assertRaises(ScreeningServiceError):
            classifier.classify("Factura adjunta", "es")


if __name__ == "__main__":
    unittest.main()
