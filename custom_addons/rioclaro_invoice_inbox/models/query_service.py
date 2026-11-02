"""Read-only, permission-scoped query tools for the assisted chatbot.

Every tool checks the caller's permission and restricts rows to what the
contact may see. Scope is decided here, in code: callers (including any future
language model) pass only a small free-text argument, never a partner, domain
or field list. Tools return a fixed set of fields.
"""

import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from odoo import api, fields, models
from odoo.tools import html2plaintext

from .date_range import DateRange, parse_date_range

# Product names are Spanish; background callers (cron, webhook) carry no language.
QUERY_LANG = "es_MX"
MAX_RESULTS = 5
MAX_TEXT_LENGTH = 80
MAX_CANDIDATES = 5
MIN_TOKEN_LENGTH = 3
_PUBLIC_CATALOG_LIST_TERMS = frozenset(
    {
        "catalogo",
        "catálogo",
        "disponible",
        "disponibles",
        "hay",
        "lista",
        "listar",
        "listado",
        "ofrece",
        "ofrecen",
        "producto",
        "productos",
        "que",
        "qué",
        "tiene",
        "tienen",
        "venden",
        "vende",
    }
)
_STOPWORDS = frozenset(
    {"para", "con", "sin", "del", "los", "las", "una", "uno", "por", "que", "de", "el", "la"}
)

# tool name -> (required permission, method name)
TOOL_REGISTRY: dict[str, tuple[str, str]] = {
    "stock_by_product": ("can_query_inventory", "_tool_stock_by_product"),
    "search_products": ("can_query_inventory", "_tool_search_products"),
    "get_product_stock": ("can_query_inventory", "_tool_get_product_stock"),
    "recent_purchases": ("can_query_purchases", "_tool_recent_purchases"),
    "recent_sales": ("can_query_sales", "_tool_recent_sales"),
    "recent_invoices": ("can_query_billing", "_tool_recent_invoices"),
    "aggregate_sales": ("can_query_sales", "_tool_aggregate_sales"),
    "aggregate_purchases": ("can_query_purchases", "_tool_aggregate_purchases"),
    "aggregate_invoices": ("can_query_billing", "_tool_aggregate_invoices"),
    "order_status": ("can_check_order_status", "_tool_order_status"),
    "top_products": ("can_query_sales", "_tool_top_products"),
    "compare_sales": ("can_query_sales", "_tool_compare_sales"),
    "product_margin": ("can_query_margins", "_tool_product_margin"),
    "open_balances": ("can_query_billing", "_tool_open_balances"),
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
            Aggregation tools take ``""`` (last 30 days), ``"<days>"`` (1-366)
            or ``"YYYY-MM-DD,YYYY-MM-DD"`` as ``text``.
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
        scoped = self._with_query_language()
        method: Callable[..., list[dict[str, Any]]] = getattr(scoped, method_name)
        try:
            rows = method(contact, clean_text)
        except ValueError:
            return {"ok": False, "error": "invalid_input"}
        return {"ok": True, "tool": tool, "rows": rows}

    @api.model
    def run_public_tool(self, tool: str, text: str = "") -> dict[str, Any]:
        """Run a query that returns only information already published publicly.

        This entry point is for anonymous website visitors. It deliberately
        does not expose inventory, prices, sales, invoices, or customer data.
        """
        clean_text = (text or "").strip()
        if len(clean_text) > MAX_TEXT_LENGTH:
            return {"ok": False, "error": "invalid_input"}
        public_methods = {
            "public_catalog": self._public_catalog,
            "public_faq": self._public_faq,
        }
        method = public_methods.get(tool)
        if not method:
            return {"ok": False, "error": "unknown_tool"}
        return {"ok": True, "tool": tool, "rows": method(clean_text)}

    @api.model
    def _public_catalog(self, text: str) -> list[dict[str, Any]]:
        """Find published products using only names and public website descriptions."""
        normalized_text = re.sub(r"[^\w\s]", " ", text.lower())
        tokens = [
            token
            for token in self._search_tokens(normalized_text)
            if token not in _PUBLIC_CATALOG_LIST_TERMS
        ]
        domain = [("is_published", "=", True)]
        if tokens:
            token_domains = [
                [
                    "|",
                    "|",
                    ("name", "ilike", token),
                    ("website_description", "ilike", token),
                    ("description_ecommerce", "ilike", token),
                ]
                for token in tokens
            ]
            match_domain = token_domains[0]
            for token_domain in token_domains[1:]:
                match_domain = ["|"] + match_domain + token_domain
            domain += match_domain
        products = self.env["product.template"].sudo().search(
            domain,
            order="name, id",
            limit=MAX_CANDIDATES * 5,
        )
        matches = []
        for product in products:
            description = " ".join(
                part
                for part in (
                    html2plaintext(product.description_ecommerce or "").strip(),
                    html2plaintext(product.website_description or "").strip(),
                )
                if part
            )
            searchable = f"{product.name} {description}".lower()
            score = sum(token in searchable for token in tokens)
            if tokens and not score:
                continue
            matches.append(
                (
                    -score,
                    product.id,
                    {
                        "name": product.name,
                        "description": description[:600],
                        "url": product.website_url,
                    },
                )
            )
        return [row for _, _, row in sorted(matches)[:MAX_CANDIDATES]]

    @api.model
    def _public_faq(self, text: str) -> list[dict[str, Any]]:
        """Search published website pages and return their public text only."""
        pages = self.env["website.page"].sudo().search(
            [("is_published", "=", True)],
            order="name, id",
            limit=100,
        )
        tokens = self._search_tokens(text)
        matches = []
        for page in pages:
            content = html2plaintext(page.view_id.arch_db or "").strip()
            searchable = f"{page.name} {content}".lower()
            score = sum(token in searchable for token in tokens)
            if tokens and not score:
                continue
            matches.append(
                (
                    -score,
                    page.id,
                    {
                        "title": page.name,
                        "url": page.url,
                        "content": content[:1200],
                    },
                )
            )
        return [row for _, _, row in sorted(matches)[:MAX_CANDIDATES]]

    @api.model
    def _with_query_language(self) -> "QueryService":
        """Return ``self`` in the Spanish query language when it is installed.

        Forcing an inactive language raises, so fresh databases that only have
        English keep the caller's context.
        """
        installed = self.env["res.lang"].sudo().search_count(
            [("code", "=", QUERY_LANG), ("active", "=", True)]
        )
        return self.with_context(lang=QUERY_LANG) if installed else self

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
                    "&",
                    *self._company_scope(contact),
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

    @staticmethod
    def _search_tokens(text: str) -> list[str]:
        """Split a phrase into significant lowercase words for AND matching."""
        return [
            token
            for token in text.lower().split()
            if len(token) >= MIN_TOKEN_LENGTH and token not in _STOPWORDS
        ]

    @api.model
    def _tool_search_products(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        """Return up to ``MAX_CANDIDATES`` product candidates, no quantities.

        An exact SKU match wins alone; otherwise every significant word must
        appear in the name or SKU. More than one row means the caller must ask
        the user to disambiguate.
        """
        if not text:
            return []
        products = self.env["product.product"].sudo().with_company(contact.company_id)
        base: list = [("is_storable", "=", True)] + self._company_scope(contact)
        exact = products.search(
            base + [("default_code", "=ilike", text)], limit=MAX_CANDIDATES
        )
        match = "exact_sku"
        if not exact:
            tokens = self._search_tokens(text)
            if not tokens:
                return []
            domain = list(base)
            for token in tokens:
                domain += ["|", ("name", "ilike", token), ("default_code", "ilike", token)]
            exact = products.search(domain, order="name, id", limit=MAX_CANDIDATES)
            match = "name"
        return [
            {
                "product_id": product.id,
                "name": product.display_name,
                "sku": product.default_code or "",
                "category": product.categ_id.display_name or "",
                "match": match,
            }
            for product in exact
        ]

    @api.model
    def _tool_get_product_stock(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        """Return live labelled stock for one product ID from ``search_products``.

        ``text`` is ``"<product_id>"`` or ``"<product_id>,<warehouse_id>"``.
        The product and warehouse are re-fetched and checked against the
        contact's company; anything else yields no rows.
        """
        parts = [part.strip() for part in text.split(",")]
        if not 1 <= len(parts) <= 2 or not all(
            part.isascii() and part.isdigit() for part in parts
        ):
            return []
        product_id = int(parts[0])
        warehouse_id = int(parts[1]) if len(parts) == 2 else 0
        company = contact.company_id
        product = (
            self.env["product.product"]
            .sudo()
            .with_company(company)
            .search(
                [("id", "=", product_id), ("is_storable", "=", True)]
                + self._company_scope(contact),
                limit=1,
            )
        )
        if not product:
            return []
        warehouse_name = ""
        if warehouse_id:
            warehouse = (
                self.env["stock.warehouse"]
                .sudo()
                .search(
                    [("id", "=", warehouse_id), ("company_id", "=", company.id)],
                    limit=1,
                )
            )
            if not warehouse:
                return []
            product = product.with_context(warehouse_id=warehouse.id)
            warehouse_name = warehouse.name
        return [
            {
                "product_id": product.id,
                "product": product.display_name,
                "warehouse": warehouse_name or "all",
                "on_hand": product.qty_available,
                "free": product.free_qty,
                "forecast": product.virtual_available,
                "uom": product.uom_id.name,
            }
        ]

    @api.model
    def _aggregate_orders(
        self,
        contact: models.Model,
        model: str,
        states: list[str],
        period: DateRange,
    ) -> list[dict[str, Any]]:
        """Sum confirmed orders per currency; Odoo, not the model, does the math."""
        domain = (
            self._partner_scope(contact)
            + self._company_scope(contact)
            + [
                ("state", "in", states),
                ("date_order", ">=", period.start_utc),
                ("date_order", "<", period.end_utc_exclusive),
            ]
        )
        groups = self.env[model].sudo()._read_group(
            domain,
            ["currency_id"],
            ["amount_untaxed:sum", "amount_total:sum", "__count"],
        )
        return [
            {
                "currency": currency.name,
                "count": count,
                "untaxed": currency.round(untaxed),
                "total": currency.round(total),
                "from": str(period.start),
                "to": str(period.end),
            }
            for currency, untaxed, total, count in groups
        ]

    @api.model
    def _tool_aggregate_sales(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        return self._aggregate_orders(
            contact, "sale.order", ["sale", "done"], parse_date_range(text)
        )

    @api.model
    def _tool_aggregate_purchases(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        return self._aggregate_orders(
            contact, "purchase.order", ["purchase", "done"], parse_date_range(text)
        )

    @api.model
    def _tool_aggregate_invoices(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        """Posted invoices per kind and currency; credit notes are subtracted."""
        period = parse_date_range(text)
        kinds = {
            "customer": ("out_invoice", "out_refund"),
            "vendor": ("in_invoice", "in_refund"),
        }
        allowed = {
            "internal": ["customer", "vendor"],
            "supplier": ["vendor"],
            "customer": ["customer"],
        }[contact.role]
        move_types = [move_type for kind in allowed for move_type in kinds[kind]]
        domain = (
            self._partner_scope(contact)
            + self._company_scope(contact)
            + [
                ("state", "=", "posted"),
                ("move_type", "in", move_types),
                ("invoice_date", ">=", period.start),
                ("invoice_date", "<=", period.end),
            ]
        )
        groups = self.env["account.move"].sudo()._read_group(
            domain,
            ["move_type", "currency_id"],
            ["amount_untaxed:sum", "amount_total:sum", "__count"],
        )
        totals: dict[tuple[str, str], dict[str, Any]] = {}
        for move_type, currency, untaxed, total, count in groups:
            kind = "customer" if move_type.startswith("out_") else "vendor"
            sign = -1 if move_type.endswith("_refund") else 1
            row = totals.setdefault(
                (kind, currency.name),
                {
                    "kind": kind,
                    "currency": currency.name,
                    "documents": 0,
                    "untaxed": 0.0,
                    "total": 0.0,
                    "from": str(period.start),
                    "to": str(period.end),
                    "_currency": currency,
                },
            )
            row["documents"] += count
            row["untaxed"] += sign * untaxed
            row["total"] += sign * total
        rows = []
        for _, row in sorted(totals.items()):
            currency = row.pop("_currency")
            row["untaxed"] = currency.round(row["untaxed"])
            row["total"] = currency.round(row["total"])
            rows.append(row)
        return rows

    @api.model
    def _sale_line_domain(self, contact: models.Model, period: DateRange) -> list[tuple]:
        """Confirmed sale lines in ``period`` limited to the contact's scope."""
        return [
            ("state", "in", ["sale", "done"]),
            ("display_type", "=", False),
            ("product_id", "!=", False),
            ("order_id.date_order", ">=", period.start_utc),
            ("order_id.date_order", "<", period.end_utc_exclusive),
            ("order_id.company_id", "in", [False, contact.company_id.id]),
        ] + [
            ("order_id." + leaf[0], leaf[1], leaf[2]) for leaf in self._partner_scope(contact)
        ]

    @api.model
    def _tool_top_products(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        """Best-selling products by untaxed revenue, with units sold."""
        period = parse_date_range(text)
        groups = self.env["sale.order.line"].sudo()._read_group(
            self._sale_line_domain(contact, period),
            ["product_id", "currency_id"],
            ["product_uom_qty:sum", "price_subtotal:sum"],
            order="price_subtotal:sum desc",
            limit=MAX_RESULTS,
        )
        return [
            {
                "product": product.display_name,
                "units": quantity,
                "untaxed": currency.round(untaxed),
                "currency": currency.name,
                "from": str(period.start),
                "to": str(period.end),
            }
            for product, currency, quantity, untaxed in groups
        ]

    @api.model
    def _tool_compare_sales(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        """Compare the last N days with the N days before them (default 7)."""
        days = 7 if not text else None
        if days is None:
            if not (text.isascii() and text.isdigit()) or not 1 <= int(text) <= 183:
                raise ValueError("days out of range")
            days = int(text)
        current = parse_date_range(str(days))
        previous = DateRange(
            current.start - timedelta(days=days), current.start - timedelta(days=1)
        )
        now_rows = {
            row["currency"]: row
            for row in self._aggregate_orders(
                contact, "sale.order", ["sale", "done"], current
            )
        }
        before_rows = {
            row["currency"]: row
            for row in self._aggregate_orders(
                contact, "sale.order", ["sale", "done"], previous
            )
        }
        rows = []
        for currency in sorted(set(now_rows) | set(before_rows)):
            now = now_rows.get(currency, {})
            before = before_rows.get(currency, {})
            now_total = now.get("untaxed", 0.0)
            before_total = before.get("untaxed", 0.0)
            rows.append(
                {
                    "currency": currency,
                    "current_from": str(current.start),
                    "current_to": str(current.end),
                    "current_orders": now.get("count", 0),
                    "current_untaxed": now_total,
                    "previous_from": str(previous.start),
                    "previous_to": str(previous.end),
                    "previous_orders": before.get("count", 0),
                    "previous_untaxed": before_total,
                    "change_untaxed": round(now_total - before_total, 2),
                    "change_pct": (
                        round((now_total - before_total) / before_total * 100, 1)
                        if before_total
                        else None
                    ),
                }
            )
        return rows

    @api.model
    def _tool_product_margin(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        """Margin per product category from untaxed revenue minus product cost.

        Lines whose product has no cost are counted but excluded from the margin.
        Internal contacts only.
        """
        if contact.role != "internal":
            return []
        period = parse_date_range(text)
        lines = self.env["sale.order.line"].sudo().search(
            self._sale_line_domain(contact, period), limit=5000
        )
        categories: dict[tuple[str, str], dict[str, Any]] = {}
        for line in lines:
            category = line.product_id.categ_id.display_name or ""
            row = categories.setdefault(
                (category, line.currency_id.name),
                {
                    "category": category,
                    "currency": line.currency_id.name,
                    "revenue": 0.0,
                    "cost": 0.0,
                    "lines_without_cost": 0,
                },
            )
            cost_each = line.product_id.with_company(contact.company_id).standard_price
            if not cost_each:
                row["lines_without_cost"] += 1
                continue
            row["revenue"] += line.price_subtotal
            row["cost"] += cost_each * line.product_uom_qty
        rows = []
        for row in categories.values():
            row["margin"] = round(row["revenue"] - row["cost"], 2)
            row["margin_pct"] = (
                round(row["margin"] / row["revenue"] * 100, 1) if row["revenue"] else None
            )
            row["revenue"] = round(row["revenue"], 2)
            row["cost"] = round(row["cost"], 2)
            row["from"] = str(period.start)
            row["to"] = str(period.end)
            rows.append(row)
        rows.sort(key=lambda row: row["margin"], reverse=True)
        return rows[:MAX_RESULTS]

    @api.model
    def _tool_open_balances(
        self, contact: models.Model, text: str
    ) -> list[dict[str, Any]]:
        """Unpaid posted invoices: receivable, payable and the overdue part.

        This is not a bank balance; it only covers open invoices. Internal only.
        """
        if contact.role != "internal":
            return []
        today = fields.Date.context_today(self)
        domain = self._company_scope(contact) + [
            ("state", "=", "posted"),
            ("move_type", "in", ["out_invoice", "out_refund", "in_invoice", "in_refund"]),
            ("payment_state", "in", ["not_paid", "partial"]),
        ]
        moves = self.env["account.move"].sudo().search(domain)
        totals: dict[tuple[str, str], dict[str, Any]] = {}
        for move in moves:
            kind = "receivable" if move.move_type.startswith("out_") else "payable"
            row = totals.setdefault(
                (kind, move.currency_id.name),
                {"kind": kind, "currency": move.currency_id.name, "open": 0.0, "overdue": 0.0},
            )
            sign = -1 if move.move_type.endswith("_refund") else 1
            row["open"] += sign * move.amount_residual
            if move.invoice_date_due and move.invoice_date_due < today:
                row["overdue"] += sign * move.amount_residual
        return [
            {**row, "open": round(row["open"], 2), "overdue": round(row["overdue"], 2)}
            for _, row in sorted(totals.items())
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
