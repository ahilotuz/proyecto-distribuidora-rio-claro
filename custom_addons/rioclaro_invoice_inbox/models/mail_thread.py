import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools.mail import html2plaintext


_logger = logging.getLogger(__name__)
_INTAKE_MODEL = "rioclaro.invoice.intake"
_SCREENING_ENABLED_PARAM = "rioclaro_invoice_inbox.screening_enabled"
_SCREENING_URL_PARAM = "rioclaro_invoice_inbox.screening_url"
_SCREENING_TIMEOUT_SECONDS = 35


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    @api.model
    def _message_route_process(self, message, message_dict, routes):
        config = self.env["ir.config_parameter"].sudo()
        if not config.get_bool(_SCREENING_ENABLED_PARAM):
            return super()._message_route_process(message, message_dict, routes)

        intake_routes = [
            route for route in routes or () if route[0] == _INTAKE_MODEL
        ]
        if not intake_routes:
            return super()._message_route_process(message, message_dict, routes)

        result = self._classify_incoming_intake_email(message_dict, config)
        accepted_routes = self._filter_invoice_intake_routes(
            routes,
            result,
            message_dict.get("message_id", "<missing>"),
        )
        return super()._message_route_process(message, message_dict, accepted_routes)

    @api.model
    def _filter_invoice_intake_routes(self, routes, result, message_id):
        if result["route"] == "review":
            _logger.info(
                "Screening routed incoming email Message-ID %s to invoice intake "
                "for manual review (label=%s).",
                message_id,
                result["label"],
            )
            return routes

        if (
            result["route"] == "inspect_attachments"
            and result["label"] == "supplier_invoice"
        ):
            return routes

        _logger.info(
            "Screening excluded incoming email Message-ID %s (route=%s, label=%s).",
            message_id,
            result["route"],
            result["label"],
        )
        return [
            route for route in routes if route[0] != _INTAKE_MODEL
        ]

    @api.model
    def _classify_incoming_intake_email(self, message_dict, config=None):
        config = config or self.env["ir.config_parameter"].sudo()
        endpoint = config.get_str(_SCREENING_URL_PARAM).strip()
        if not endpoint:
            raise UserError(
                _("Email screening is enabled, but its Function URL is not configured.")
            )

        body = html2plaintext(message_dict.get("body") or "").strip()
        if not body:
            body = (message_dict.get("subject") or "").strip()
        if not body:
            raise UserError(
                _("Email screening cannot classify a message with no body or subject.")
            )

        payload = {
            "body": body[:100_000],
            "hasAttachments": bool(message_dict.get("attachments")),
            "language": "es",
        }
        headers = {"Content-Type": "application/json"}
        function_key = os.environ.get("RIOCLARO_SCREENING_FUNCTION_KEY", "").strip()
        if function_key:
            headers["x-functions-key"] = function_key

        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=_SCREENING_TIMEOUT_SECONDS) as response:
                result = json.loads(response.read())
        except HTTPError as error:
            _logger.error(
                "Email screening returned HTTP %s for Message-ID %s.",
                error.code,
                message_dict.get("message_id", "<missing>"),
            )
            raise UserError(
                _("Email screening failed with HTTP %(status)s.")
                % {"status": error.code}
            ) from error
        except (URLError, TimeoutError) as error:
            _logger.error(
                "Email screening could not be reached for Message-ID %s.",
                message_dict.get("message_id", "<missing>"),
            )
            raise UserError(_("Email screening could not be reached.")) from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            _logger.error(
                "Email screening returned invalid JSON for Message-ID %s.",
                message_dict.get("message_id", "<missing>"),
            )
            raise UserError(_("Email screening returned an invalid response.")) from error

        route = result.get("route") if isinstance(result, dict) else None
        label = result.get("label") if isinstance(result, dict) else None
        confidence = result.get("confidence") if isinstance(result, dict) else None
        if (
            route not in {"ignore", "review", "inspect_attachments"}
            or label not in {"supplier_invoice", "not_invoice"}
            or not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0.0 <= confidence <= 1.0
        ):
            raise UserError(_("Email screening returned an invalid classification."))
        return {
            "route": route,
            "label": label,
            "confidence": confidence,
        }
