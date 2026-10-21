"""Read-only, permission-scoped query tools for the assisted chatbot.

Every tool checks the caller's permission and restricts rows to what the
contact may see. Scope is decided here, in code: callers (including any future
language model) pass only a small free-text argument, never a partner, domain
or field list. Tools return a fixed set of fields.
"""

from collections.abc import Callable
from typing import Any

from odoo import api, models

MAX_RESULTS = 5
MAX_TEXT_LENGTH = 80

# tool name -> (required permission, method name)
TOOL_REGISTRY: dict[str, tuple[str, str]] = {
    "stock_by_product": ("can_query_inventory", "_tool_stock_by_product"),
    "recent_purchases": ("can_query_purchases", "_tool_recent_purchases"),
    "recent_sales": ("can_query_sales", "_tool_recent_sales"),
    "recent_invoices": ("can_query_billing", "_tool_recent_invoices"),
    "order_status": ("can_check_order_status", "_tool_order_status"),
}


class QueryService(models.AbstractModel):
    """Entry point that dispatches authorized read-only queries."""

    _name = "rioclaro.query.service"
    _description = "Assisted Query Service"

    @api.model
    def run_tool(
        self, telegram_user_id: int | str, tool: str, text: str = ""
    ) -> dict[str, Any]:
        """Run a registered tool on behalf of a Telegram contact.

        Args:
            telegram_user_id: Numeric Telegram ID of the requester.
            tool: A key of ``TOOL_REGISTRY``.
            text: Optional free-text argument (product name, order reference).

        Returns:
            ``{"ok": True, "tool": ..., "rows": [...]}`` or
            ``{"ok": False, "error": "denied" | "unknown_tool" | "invalid_input"}``.
        """
        entry = TOOL_REGISTRY.get(tool)
        if entry is None:
            return {"ok": False, "error": "unknown_tool"}
        permission, method_name = entry
        contacts = self.env["rioclaro.telegram.contact"]
        contact = contacts.get_active_contact(telegram_user_id)
        if not contact or not contact[permission]:
            return {"ok": False, "error": "denied"}
        clean_text = (text or "").strip()
        if len(clean_text) > MAX_TEXT_LENGTH:
            return {"ok": False, "error": "invalid_input"}
        method: Callable[..., list[dict[str, Any]]] = getattr(self, method_name)
        return {"ok": True, "tool": tool, "rows": method(contact, clean_text)}

    @api.model
    def _partner_scope(self, contact: models.Model) -> list[tuple]:
        """Domain on ``partner_id`` limiting external roles to their own partner.

        Internal contacts see all partners. An external contact without a
        partner sees nothing.
        """
        if contact.role == "internal":
            return []
        if not contact.partner_id:
            return [("id", "=", 0)]
        commercial = contact.partner_id.commercial_partner_id
        return [("partner_id", "child_of", commercial.id)]

    @api.model
    def _company_scope(self, contact: models.Model) -> list[tuple]:
        return [("company_id", "in", [False, contact.company_id.id])]

    @api.model
    def _tool_stock_by_product(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        if not text:
            return []
        products = (
            self.env["product.product"]
            .sudo()
            .with_company(contact.company_id)
            .search(
                [
                    "&",
                    ("is_storable", "=", True),
                    "|",
                    ("name", "ilike", text),
                    ("default_code", "ilike", text),
                ],
                limit=MAX_RESULTS,
            )
        )
        return [
            {
                "product": product.display_name,
                "on_hand": product.qty_available,
                "available": product.free_qty,
                "uom": product.uom_id.name,
            }
            for product in products
        ]

    @api.model
    def _tool_recent_purchases(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        domain = self._partner_scope(contact) + self._company_scope(contact)
        orders = (
            self.env["purchase.order"]
            .sudo()
            .search(domain, order="date_order desc, id desc", limit=MAX_RESULTS)
        )
        return [self._order_row(order) for order in orders]

    @api.model
    def _tool_recent_sales(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        domain = self._partner_scope(contact) + self._company_scope(contact)
        orders = (
            self.env["sale.order"]
            .sudo()
            .search(domain, order="date_order desc, id desc", limit=MAX_RESULTS)
        )
        return [self._order_row(order) for order in orders]

    @api.model
    def _tool_recent_invoices(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        move_types = {
            "internal": ["in_invoice", "out_invoice"],
            "supplier": ["in_invoice"],
            "customer": ["out_invoice"],
        }[contact.role]
        domain = (
            self._partner_scope(contact)
            + self._company_scope(contact)
            + [("move_type", "in", move_types), ("state", "=", "posted")]
        )
        moves = (
            self.env["account.move"]
            .sudo()
            .search(domain, order="invoice_date desc, id desc", limit=MAX_RESULTS)
        )
        return [
            {
                "number": move.name,
                "type": move.move_type,
                "partner": move.partner_id.name,
                "date": str(move.invoice_date or ""),
                "total": move.amount_total,
                "currency": move.currency_id.name,
                "payment_state": move.payment_state,
            }
            for move in moves
        ]

    @api.model
    def _tool_order_status(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        if not text:
            return []
        models_by_role = {
            "internal": ["sale.order", "purchase.order"],
            "supplier": ["purchase.order"],
            "customer": ["sale.order"],
        }[contact.role]
        domain = (
            self._partner_scope(contact)
            + self._company_scope(contact)
            + [("name", "=ilike", text)]
        )
        rows: list[dict[str, Any]] = []
        for model_name in models_by_role:
            orders = self.env[model_name].sudo().search(domain, limit=MAX_RESULTS)
            rows.extend(self._order_row(order) for order in orders)
        return rows[:MAX_RESULTS]

    @api.model
    def _order_row(self, order: models.Model) -> dict[str, Any]:
        """Fixed, minimal projection of a sale or purchase order."""
        return {
            "reference": order.name,
            "partner": order.partner_id.name,
            "date": str(order.date_order or ""),
            "state": order.state,
            "total": order.amount_total,
            "currency": order.currency_id.name,
        }
