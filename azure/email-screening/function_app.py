import json
import logging

import azure.functions as func

from screening import (
    ScreeningConfigurationError,
    ScreeningServiceError,
    build_classifier,
    decide_route,
    read_confidence_threshold,
)

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="screen-email", methods=["POST"])
def screen_email(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = req.get_json()
    except ValueError:
        return _json_response({"error": "Request body must be valid JSON."}, 400)

    body = payload.get("body") if isinstance(payload, dict) else None
    has_attachments = (
        payload.get("hasAttachments") if isinstance(payload, dict) else None
    )
    language = payload.get("language", "es") if isinstance(payload, dict) else "es"
    if not isinstance(body, str) or not body.strip():
        return _json_response({"error": "A non-empty email body is required."}, 400)
    if len(body) > 100_000:
        return _json_response(
            {"error": "Email body exceeds the 100000-character limit."},
            413,
        )
    if not isinstance(has_attachments, bool):
        return _json_response(
            {"error": "hasAttachments must be a boolean."},
            400,
        )
    if not isinstance(language, str) or not language:
        return _json_response({"error": "language must be a language code."}, 400)

    try:
        classifier = build_classifier()
        classification = classifier.classify(body.strip(), language)
        decision = decide_route(
            classification,
            has_attachments,
            read_confidence_threshold(),
        )
    except ScreeningConfigurationError as error:
        logging.error("Email screening is not configured: %s", error)
        return _json_response({"error": "Email screening is not configured."}, 503)
    except ScreeningServiceError as error:
        logging.error("Email classification failed: %s", error)
        return _json_response({"error": "Email classification failed."}, 502)

    return _json_response(
        {
            "route": decision.route,
            "label": decision.label,
            "confidence": decision.confidence,
        },
        200,
    )


def _json_response(payload: dict, status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload),
        status_code=status_code,
        mimetype="application/json",
    )
