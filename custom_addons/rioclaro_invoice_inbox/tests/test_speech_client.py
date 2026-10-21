"""Tests for Azure AI Speech REST calls."""

import json
from unittest.mock import Mock, patch

from odoo.addons.rioclaro_invoice_inbox.models.speech_client import (
    AzureSpeechClient,
    SpeechError,
)
from odoo.tests import TransactionCase, tagged

_URLOPEN = "odoo.addons.rioclaro_invoice_inbox.models.speech_client.urlopen"


def _response(body: bytes) -> Mock:
    response = Mock()
    response.read.return_value = body
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


@tagged("post_install", "-at_install")
class TestAzureSpeechClient(TransactionCase):
    def setUp(self):
        super().setUp()
        self.client = AzureSpeechClient(
            endpoint="https://canadacentral.api.cognitive.microsoft.com/",
            region="canadacentral",
            api_key="test-key",
        )

    def test_rejects_endpoint_region_mismatch(self):
        with self.assertRaises(ValueError):
            AzureSpeechClient(
                endpoint="https://eastus.api.cognitive.microsoft.com/",
                region="canadacentral",
                api_key="test-key",
            )

    @patch(_URLOPEN)
    def test_transcribes_ogg_voice_note(self, urlopen_mock):
        urlopen_mock.return_value = _response(
            json.dumps(
                {
                    "RecognitionStatus": "Success",
                    "DisplayText": "¿Qué existencias hay?",
                }
            ).encode()
        )

        result = self.client.transcribe(b"ogg-opus-content")

        self.assertEqual(result, "¿Qué existencias hay?")
        request = urlopen_mock.call_args.args[0]
        self.assertIn(
            "canadacentral.api.cognitive.microsoft.com/stt/speech/recognition",
            request.full_url,
        )
        self.assertIn("language=es-MX", request.full_url)
        self.assertEqual(request.get_header("Content-type"), "audio/ogg; codecs=opus")

    @patch(_URLOPEN)
    def test_transcription_no_match_raises_explicit_error(self, urlopen_mock):
        urlopen_mock.return_value = _response(
            json.dumps({"RecognitionStatus": "NoMatch"}).encode()
        )

        with self.assertRaises(SpeechError):
            self.client.transcribe(b"ogg-opus-content")

    @patch(_URLOPEN)
    def test_synthesizes_mp3_from_escaped_ssml(self, urlopen_mock):
        urlopen_mock.return_value = _response(b"fake-mp3")

        result = self.client.synthesize("Stock & ventas")

        self.assertEqual(result, b"fake-mp3")
        request = urlopen_mock.call_args.args[0]
        self.assertIn("canadacentral.tts.speech.microsoft.com", request.full_url)
        self.assertIn(b"Stock &amp; ventas", request.data)
        self.assertEqual(
            request.get_header("X-microsoft-outputformat"),
            "audio-24khz-48kbitrate-mono-mp3",
        )
