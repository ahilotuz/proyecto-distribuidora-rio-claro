"""Minimal audit records for Telegram assisted-query requests."""

from odoo import api, fields, models

from .query_service import PUBLIC_TOOL_NAMES, TOOL_REGISTRY


class AssistedQueryAudit(models.Model):
    """Record requester, selected tool, and decision without storing chat text."""

    _name = "rioclaro.assisted.query.audit"
    _description = "Assisted Query Audit"
    _order = "request_at desc, id desc"

    telegram_user_id = fields.Char(required=True, index=True)
    contact_id = fields.Many2one(
        "rioclaro.telegram.contact",
        ondelete="set null",
        index=True,
    )
    company_id = fields.Many2one("res.company", index=True)
    company_ids = fields.Many2many(
        "res.company",
        relation="rioclaro_assisted_query_audit_company_rel",
        column1="audit_id",
        column2="company_id",
        string="Queried Companies",
    )
    source_message_id = fields.Char(required=True, index=True, copy=False)
    input_type = fields.Selection(
        [("text", "Text"), ("voice", "Voice")],
        required=True,
        default="text",
        index=True,
    )
    tool = fields.Selection(
        [
            (name, name.replace("_", " ").title())
            for name in (*TOOL_REGISTRY, *sorted(PUBLIC_TOOL_NAMES))
        ]
        + [("help", "Help"), ("onboarding", "Onboarding")],
        index=True,
    )
    outcome = fields.Selection(
        [
            ("allowed", "Allowed"),
            ("denied", "Denied"),
            ("rate_limited", "Rate Limited"),
            ("scope_required", "Company Scope Required"),
            ("invalid", "Invalid Request"),
            ("error", "Error"),
        ],
        required=True,
        index=True,
    )
    request_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)

    _source_message_id_unique = models.Constraint(
        "unique(source_message_id)",
        "This Telegram query has already been audited.",
    )

    @api.model
    def log_request(
        self,
        *,
        telegram_user_id: int | str,
        source_message_id: str,
        outcome: str,
        contact: models.Model | None = None,
        tool: str | None = None,
        input_type: str = "text",
        company_ids: list[int] | tuple[int, ...] | None = None,
    ) -> models.Model:
        """Create an audit entry without persisting request or response text."""
        selected_company_ids = list(company_ids or [])
        return self.sudo().create(
            {
                "telegram_user_id": str(telegram_user_id),
                "source_message_id": source_message_id,
                "outcome": outcome,
                "contact_id": contact.id if contact else False,
                "company_id": (
                    selected_company_ids[0]
                    if selected_company_ids
                    else contact.company_id.id if contact else False
                ),
                "company_ids": [(6, 0, selected_company_ids)],
                "input_type": input_type if input_type in {"text", "voice"} else "text",
                "tool": (
                    tool
                    if tool in TOOL_REGISTRY
                    or tool in PUBLIC_TOOL_NAMES
                    or tool in {"help", "onboarding"}
                    else False
                ),
            }
        )
