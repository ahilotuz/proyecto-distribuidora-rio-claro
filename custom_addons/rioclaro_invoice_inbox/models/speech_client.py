"""Azure AI Speech REST client for voice transcription and speech synthesis."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape, quoteattr


class SpeechError(Exception):
    """Raised when Azure Speech fails or returns an invalid response."""


class AzureSpeechClient:
    """Transcribe Telegram OGG/Opus voice notes and synthesize MP3 replies."""

    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        api_key: str,
        request_timeout: float = 45.0,
    ) -> None:
        parsed_endpoint = urlsplit(endpoint.strip())
        normalized_region = region.strip().lower()
        if (
            parsed_endpoint.scheme != "https"
            or not parsed_endpoint.hostname
            or parsed_endpoint.username
            or parsed_endpoint.password
            or parsed_endpoint.query
            or parsed_endpoint.fragment
        ):
            raise ValueError("Azure Speech endpoint must be an HTTPS URL.")
        if not normalized_region.replace("-", "").isalnum():
            raise ValueError("Azure Speech region is invalid.")
        if parsed_endpoint.hostname.lower() != (
            f"{normalized_region}.api.cognitive.microsoft.com"
        ):
            raise ValueError("Azure Speech endpoint does not match the configured region.")
        if not api_key.strip():
            raise ValueError("Azure Speech API key must not be empty.")
        if request_timeout <= 0:
            raise ValueError("Azure Speech timeout must be positive.")
        self._region = normalized_region
        self._endpoint = endpoint.strip().rstrip("/")
        self._api_key = api_key.strip()
        self._request_timeout = request_timeout

    def transcribe(self, audio: bytes, *, language: str = "es-MX") -> str:
        """Transcribe one OGG/Opus voice note using short-audio recognition."""
        if not audio:
            raise ValueError("Audio content must not be empty.")
        query = urlencode({"language": language, "format": "detailed"})
        url = (
            f"{self._endpoint}/stt/speech/recognition/conversation"
            f"/cognitiveservices/v1?{query}"
        )
        request = Request(
            url,
            data=audio,
            headers={
                "Content-Type": "audio/ogg; codecs=opus",
                "Ocp-Apim-Subscription-Key": self._api_key,
                "Accept": "application/json",
            },
            method="POST",
        )
        payload = self._json_request(request, "transcription")
        display_text = payload.get("DisplayText")
        if not isinstance(display_text, str) or not display_text.strip():
            nbest = payload.get("NBest")
            display_text = (
                nbest[0].get("Display")
                if isinstance(nbest, list) and nbest and isinstance(nbest[0], dict)
                else None
            )
        if not isinstance(display_text, str) or not display_text.strip():
            raise SpeechError("Azure Speech did not recognize speech in the audio.")
        return display_text.strip()

    def synthesize(
        self,
        text: str,
        *,
        language: str = "es-MX",
        voice: str = "es-MX-DaliaNeural",
    ) -> bytes:
        """Synthesize a short text reply as mono MP3 audio."""
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Speech text must not be empty.")
        ssml = (
            f"<speak version='1.0' xml:lang={quoteattr(language)}>"
            f"<voice name={quoteattr(voice)}>"
            f"{escape(clean_text)}</voice></speak>"
        ).encode()
        request = Request(
            f"https://{self._region}.tts.speech.microsoft.com/cognitiveservices/v1",
            data=ssml,
            headers={
                "Content-Type": "application/ssml+xml",
                "Ocp-Apim-Subscription-Key": self._api_key,
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "rioclaro-invoice-inbox",
            },
            method="POST",
        )
        audio = self._binary_request(request, "speech synthesis")
        if not audio:
            raise SpeechError("Azure Speech returned an empty audio response.")
        return audio

    def _json_request(self, request: Request, operation: str) -> dict:
        content = self._binary_request(request, operation)
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise SpeechError(f"Azure Speech {operation} returned invalid JSON.") from error
        if not isinstance(payload, dict):
            raise SpeechError(f"Azure Speech {operation} returned an invalid response.")
        status = payload.get("RecognitionStatus")
        if status not in (None, "Success"):
            raise SpeechError("Azure Speech could not recognize the voice note.")
        return payload

    def _binary_request(self, request: Request, operation: str) -> bytes:
        try:
            with urlopen(request, timeout=self._request_timeout) as response:
                return response.read()
        except HTTPError as error:
            raise SpeechError(
                f"Azure Speech {operation} returned HTTP {error.code}."
            ) from error
        except (URLError, TimeoutError) as error:
            raise SpeechError(f"Azure Speech {operation} could not be reached.") from error
