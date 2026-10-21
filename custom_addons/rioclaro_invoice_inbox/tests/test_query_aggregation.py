"""Tests for the sales, purchase and invoice aggregation tools."""

from odoo.tests import TransactionCase, tagged

RANGE = "2026-01-01,2026-01-31"


@tagged("post_install", "-at_install")
class TestQueryAggregation(TransactionCase):
    def setUp(self):
        super().setUp()
        env = self.env
        self.service = env["rioclaro.query.service"]
        partners = env["res.partner"]
        self.customer_a = partners.create({"name": "Agg Customer A"})
        self.customer_b = partners.create({"name": "Agg Customer B"})
        self.supplier_a = partners.create({"name": "Agg Supplier A"})
        self.product = env["product.product"].create(
            {"name": "Agg Product", "type": "consu", "list_price": 100}
        )
        contacts = env["rioclaro.telegram.contact"]
        self.internal = contacts.create(
            {"name": "i", "telegram_user_id": "11", "role": "internal"}
        )
        contacts.create(
            {
                "name": "c",
                "telegram_user_id": "12",
                "role": "customer",
                "partner_id": self.customer_a.id,
            }
        )
        contacts.create(
            {
                "name": "s",
                "telegram_user_id": "13",
                "role": "supplier",
                "partner_id": self.supplier_a.id,
            }
        )

    def _sale(self, partner, qty: float, date: str, confirm: bool = True):
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "date_order": date,
                "order_line": [
                    (0, 0, {"product_id": self.product.id, "product_uom_qty": qty, "price_unit": 100})
                ],
            }
        )
        if confirm:
            order.action_confirm()
            order.date_order = date
        return order

    def _purchase(self, partner, qty: float, date: str, confirm: bool = True):
        order = self.env["purchase.order"].create(
            {
                "partner_id": partner.id,
                "date_order": date,
                "order_line": [
                    (0, 0, {"product_id": self.product.id, "product_qty": qty, "price_unit": 50})
                ],
            }
        )
        if confirm:
            order.button_confirm()
            order.date_order = date
        return order

    def _invoice(self, move_type: str, partner, amount: float, date: str, post=True):
        move = self.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": partner.id,
                "invoice_date": date,
                "invoice_line_ids": [
                    (0, 0, {"name": "Item", "quantity": 1, "price_unit": amount})
                ],
            }
        )
        if post:
            move.action_post()
        return move

    def _rows(self, user_id: int, tool: str, text: str = RANGE) -> list[dict]:
        result = self.service.run_tool(user_id, tool, text)
        self.assertTrue(result["ok"], result)
        return result["rows"]

    def test_sales_sum_only_confirmed_in_range(self):
        self._sale(self.customer_a, 2, "2026-01-10 12:00:00")
        self._sale(self.customer_a, 1, "2026-01-20 12:00:00")
        self._sale(self.customer_a, 5, "2026-01-12 12:00:00", confirm=False)
        self._sale(self.customer_a, 7, "2026-02-12 12:00:00")
        rows = self._rows(12, "aggregate_sales")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[0]["untaxed"], 300.0)
        self.assertGreaterEqual(rows[0]["total"], 300.0)

    def test_sales_date_boundary_uses_local_timezone(self):
        # 2026-02-01 03:00 UTC is still 2026-01-31 21:00 in Mexico City.
        self._sale(self.customer_a, 1, "2026-02-01 03:00:00")
        # 2026-01-01 03:00 UTC is 2025-12-31 21:00 locally: outside the range.
        self._sale(self.customer_a, 4, "2026-01-01 03:00:00")
        rows = self._rows(12, "aggregate_sales")
        self.assertEqual(rows[0]["count"], 1)
        self.assertEqual(rows[0]["untaxed"], 100.0)

    def test_sales_customer_isolated_internal_sees_all(self):
        self._sale(self.customer_a, 1, "2026-01-10 12:00:00")
        self._sale(self.customer_b, 3, "2026-01-10 12:00:00")
        self.assertEqual(self._rows(12, "aggregate_sales")[0]["untaxed"], 100.0)
        internal = self._rows(11, "aggregate_sales")
        self.assertEqual(sum(r["untaxed"] for r in internal), 400.0)

    def test_sales_other_company_excluded(self):
        other = self.env["res.company"].create({"name": "Agg Other Co"})
        order = self._sale(self.customer_a, 1, "2026-01-10 12:00:00")
        order.company_id = other
        self.assertEqual(self._rows(12, "aggregate_sales"), [])

    def test_empty_result_is_empty_list(self):
        self.assertEqual(self._rows(12, "aggregate_sales"), [])
        self.assertEqual(self._rows(13, "aggregate_purchases"), [])
        self.assertEqual(self._rows(12, "aggregate_invoices"), [])

    def test_purchases_supplier_scope_and_states(self):
        self._purchase(self.supplier_a, 2, "2026-01-10 12:00:00")
        self._purchase(self.supplier_a, 9, "2026-01-11 12:00:00", confirm=False)
        other = self.env["res.partner"].create({"name": "Agg Supplier B"})
        self._purchase(other, 4, "2026-01-12 12:00:00")
        rows = self._rows(13, "aggregate_purchases")
        self.assertEqual((rows[0]["count"], rows[0]["untaxed"]), (1, 100.0))
        self.assertEqual(sum(r["untaxed"] for r in self._rows(11, "aggregate_purchases")), 300.0)

    def test_invoices_subtract_credit_notes_and_skip_drafts(self):
        self._invoice("out_invoice", self.customer_a, 500, "2026-01-10")
        self._invoice("out_refund", self.customer_a, 120, "2026-01-15")
        self._invoice("out_invoice", self.customer_a, 900, "2026-01-16", post=False)
        self._invoice("out_invoice", self.customer_a, 900, "2026-02-16")
        rows = self._rows(12, "aggregate_invoices")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "customer")
        self.assertEqual(rows[0]["documents"], 2)
        self.assertEqual(rows[0]["untaxed"], 380.0)

    def test_invoices_role_kinds_and_partner_isolation(self):
        self._invoice("out_invoice", self.customer_a, 100, "2026-01-10")
        self._invoice("out_invoice", self.customer_b, 200, "2026-01-10")
        self._invoice("in_invoice", self.supplier_a, 50, "2026-01-10")
        self.assertEqual([r["kind"] for r in self._rows(12, "aggregate_invoices")], ["customer"])
        self.assertEqual(self._rows(12, "aggregate_invoices")[0]["untaxed"], 100.0)
        self.assertEqual([r["kind"] for r in self._rows(13, "aggregate_invoices")], ["vendor"])
        kinds = {r["kind"]: r["untaxed"] for r in self._rows(11, "aggregate_invoices")}
        self.assertEqual(kinds, {"customer": 300.0, "vendor": 50.0})

    def test_totals_are_grouped_per_currency(self):
        usd = self.env.ref("base.USD")
        eur = self.env.ref("base.EUR")
        (usd | eur).write({"active": True})
        for currency, amount in ((usd, 100), (eur, 40)):
            move = self.env["account.move"].create(
                {
                    "move_type": "out_invoice",
                    "partner_id": self.customer_a.id,
                    "invoice_date": "2026-01-10",
                    "currency_id": currency.id,
                    "invoice_line_ids": [
                        (0, 0, {"name": "x", "quantity": 1, "price_unit": amount})
                    ],
                }
            )
            move.action_post()
        by_currency = {r["currency"]: r["untaxed"] for r in self._rows(12, "aggregate_invoices")}
        self.assertEqual(by_currency, {"USD": 100.0, "EUR": 40.0})

    def test_invalid_ranges_are_rejected(self):
        for tool in ("aggregate_sales", "aggregate_purchases", "aggregate_invoices"):
            for text in ("999", "abc", "2020-01-01,2026-01-01", "2026-02-01,2026-01-01"):
                result = self.service.run_tool(11, tool, text)
                self.assertEqual(result["error"], "invalid_input", (tool, text))

    def test_permissions_are_enforced_per_tool(self):
        self.assertEqual(self.service.run_tool(12, "aggregate_purchases")["error"], "denied")
        self.assertEqual(self.service.run_tool(13, "aggregate_sales")["error"], "denied")
        self.internal.can_query_billing = False
        self.assertEqual(self.service.run_tool(11, "aggregate_invoices")["error"], "denied")
        self.assertEqual(self.service.run_tool(999, "aggregate_sales")["error"], "denied")

    def test_default_range_works(self):
        for tool in ("aggregate_sales", "aggregate_purchases", "aggregate_invoices"):
            self.assertTrue(self.service.run_tool(11, tool)["ok"])
            self.assertTrue(self.service.run_tool(11, tool, "90")["ok"])
