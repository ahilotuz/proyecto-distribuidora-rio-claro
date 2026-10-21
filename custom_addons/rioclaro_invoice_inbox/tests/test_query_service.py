"""Tests for the read-only assisted query service."""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestQueryService(TransactionCase):
    def setUp(self):
        super().setUp()
        env = self.env
        self.service = env["rioclaro.query.service"]
        contacts = env["rioclaro.telegram.contact"]
        partners = env["res.partner"]
        self.supplier_a = partners.create({"name": "Supplier A"})
        self.supplier_b = partners.create({"name": "Supplier B"})
        self.customer_a = partners.create({"name": "Customer A"})
        self.customer_b = partners.create({"name": "Customer B"})
        self.product = env["product.product"].create(
            {"name": "Agua Rio Claro 20L", "is_storable": True, "type": "consu"}
        )
        warehouse_loc = env["stock.warehouse"].search([], limit=1).lot_stock_id
        env["stock.quant"].with_context(inventory_mode=True).create(
            {
                "product_id": self.product.id,
                "location_id": warehouse_loc.id,
                "inventory_quantity": 12,
            }
        ).action_apply_inventory()
        self.po_a = self._purchase(self.supplier_a)
        self.po_b = self._purchase(self.supplier_b)
        self.so_a = self._sale(self.customer_a)
        self.so_b = self._sale(self.customer_b)
        self.internal = self._contact("1", "internal")
        self.supplier = self._contact("2", "supplier", partner_id=self.supplier_a.id)
        self.customer = self._contact("3", "customer", partner_id=self.customer_a.id)
        self.contacts = contacts

    def _contact(self, user_id: str, role: str, **extra):
        return self.env["rioclaro.telegram.contact"].create(
            {
                "name": f"c{user_id}",
                "telegram_user_id": user_id,
                "role": role,
                **extra,
            }
        )

    def _purchase(self, partner):
        return self.env["purchase.order"].create(
            {
                "partner_id": partner.id,
                "order_line": [
                    (0, 0, {"product_id": self.product.id, "product_qty": 1})
                ],
            }
        )

    def _sale(self, partner):
        return self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "order_line": [
                    (0, 0, {"product_id": self.product.id, "product_uom_qty": 1})
                ],
            }
        )

    def _references(self, result) -> set[str]:
        return {row["reference"] for row in result["rows"]}

    def test_unknown_tool_is_rejected(self):
        self.assertEqual(
            self.service.run_tool(1, "drop_tables"),
            {"ok": False, "error": "unknown_tool"},
        )

    def test_unknown_user_is_denied(self):
        result = self.service.run_tool(999, "recent_sales")
        self.assertEqual(result, {"ok": False, "error": "denied"})

    def test_inactive_contact_is_denied(self):
        self.internal.active = False
        self.assertFalse(self.service.run_tool(1, "recent_sales")["ok"])

    def test_revoked_permission_is_denied(self):
        self.internal.can_query_sales = False
        result = self.service.run_tool(1, "recent_sales")
        self.assertEqual(result["error"], "denied")

    def test_oversized_text_is_rejected(self):
        result = self.service.run_tool(1, "stock_by_product", "x" * 500)
        self.assertEqual(result["error"], "invalid_input")

    def test_stock_for_internal_user(self):
        result = self.service.run_tool(1, "stock_by_product", "agua rio")
        self.assertTrue(result["ok"])
        self.assertEqual(
            [row["product"] for row in result["rows"]],
            [self.product.display_name],
        )
        self.assertEqual(result["rows"][0]["on_hand"], 12)

    def test_customer_cannot_query_inventory_by_default(self):
        result = self.service.run_tool(3, "stock_by_product", "agua")
        self.assertEqual(result["error"], "denied")

    def test_stock_without_text_returns_nothing(self):
        self.assertEqual(self.service.run_tool(1, "stock_by_product")["rows"], [])

    def test_supplier_sees_only_own_purchases(self):
        result = self.service.run_tool(2, "recent_purchases")
        self.assertEqual(self._references(result), {self.po_a.name})

    def test_customer_sees_only_own_sales(self):
        result = self.service.run_tool(3, "recent_sales")
        self.assertEqual(self._references(result), {self.so_a.name})

    def test_internal_sees_all_sales_and_purchases(self):
        sales = self.service.run_tool(1, "recent_sales")
        purchases = self.service.run_tool(1, "recent_purchases")
        self.assertTrue({self.so_a.name, self.so_b.name} <= self._references(sales))
        self.assertTrue(
            {self.po_a.name, self.po_b.name} <= self._references(purchases)
        )

    def test_supplier_cannot_see_sales(self):
        self.assertEqual(
            self.service.run_tool(2, "recent_sales")["error"], "denied"
        )

    def test_order_status_cross_partner_leak_is_blocked(self):
        own = self.service.run_tool(3, "order_status", self.so_a.name)
        other = self.service.run_tool(3, "order_status", self.so_b.name)
        self.assertEqual(self._references(own), {self.so_a.name})
        self.assertEqual(other["rows"], [])

    def test_supplier_order_status_ignores_sale_orders(self):
        self.supplier.can_check_order_status = True
        result = self.service.run_tool(2, "order_status", self.so_a.name)
        self.assertEqual(result["rows"], [])

    def test_external_contact_without_partner_cannot_exist(self):
        with self.assertRaises(ValidationError):
            self._contact("4", "customer")

    def test_order_rows_expose_only_fixed_fields(self):
        row = self.service.run_tool(1, "recent_sales")["rows"][0]
        self.assertEqual(
            set(row),
            {"reference", "partner", "date", "state", "total", "currency"},
        )

    def test_invoices_scoped_by_role_and_partner(self):
        invoice_a = self._posted_invoice("out_invoice", self.customer_a)
        invoice_b = self._posted_invoice("out_invoice", self.customer_b)
        bill_a = self._posted_invoice("in_invoice", self.supplier_a)
        customer_view = self.service.run_tool(3, "recent_invoices")
        numbers = {row["number"] for row in customer_view["rows"]}
        self.assertEqual(numbers, {invoice_a.name})
        supplier_view = self.service.run_tool(2, "recent_invoices")
        self.assertEqual(
            {row["number"] for row in supplier_view["rows"]}, {bill_a.name}
        )
        internal_numbers = {
            row["number"] for row in self.service.run_tool(1, "recent_invoices")["rows"]
        }
        self.assertTrue({invoice_a.name, invoice_b.name, bill_a.name} <= internal_numbers)

    def _posted_invoice(self, move_type: str, partner):
        move = self.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": partner.id,
                "invoice_date": "2026-01-15",
                "invoice_line_ids": [
                    (0, 0, {"name": "Item", "quantity": 1, "price_unit": 100})
                ],
            }
        )
        move.action_post()
        return move
