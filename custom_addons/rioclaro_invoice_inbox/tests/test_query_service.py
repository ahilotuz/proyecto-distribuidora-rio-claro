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

    def _second_company_product(self):
        other = self.env["res.company"].create({"name": "Other Co"})
        return other, self.env["product.product"].create(
            {
                "name": "Producto Otra Empresa",
                "is_storable": True,
                "type": "consu",
                "company_id": other.id,
            }
        )

    def test_search_products_by_name_returns_ids_without_quantities(self):
        rows = self.service.run_tool(1, "search_products", "agua 20L")["rows"]
        self.assertEqual([r["product_id"] for r in rows], [self.product.id])
        self.assertEqual(rows[0]["match"], "name")
        self.assertNotIn("on_hand", rows[0])

    def test_search_products_exact_sku_wins(self):
        self.product.default_code = "AGUA-20"
        other = self.env["product.product"].create(
            {"name": "Agua AGUA-20 extra", "is_storable": True, "type": "consu"}
        )
        rows = self.service.run_tool(1, "search_products", "agua-20")["rows"]
        self.assertEqual([r["product_id"] for r in rows], [self.product.id])
        self.assertEqual(rows[0]["match"], "exact_sku")
        self.assertNotIn(other.id, [r["product_id"] for r in rows])

    def test_search_products_ambiguous_returns_several_capped(self):
        for i in range(7):
            self.env["product.product"].create(
                {"name": f"Silla modelo {i}", "is_storable": True, "type": "consu"}
            )
        rows = self.service.run_tool(1, "search_products", "silla")["rows"]
        self.assertEqual(len(rows), 5)

    def test_search_products_no_match_and_only_stopwords(self):
        self.assertEqual(self.service.run_tool(1, "search_products", "xyzzy")["rows"], [])
        self.assertEqual(self.service.run_tool(1, "search_products", "para de")["rows"], [])
        self.assertEqual(self.service.run_tool(1, "search_products")["rows"], [])

    def test_search_products_denied_for_customer(self):
        self.assertEqual(
            self.service.run_tool(3, "search_products", "agua")["error"], "denied"
        )

    def test_search_products_excludes_other_company(self):
        other, product = self._second_company_product()
        rows = self.service.run_tool(1, "search_products", "otra empresa")["rows"]
        self.assertNotIn(product.id, [r["product_id"] for r in rows])

    def test_get_product_stock_labelled_values(self):
        rows = self.service.run_tool(1, "get_product_stock", str(self.product.id))["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["on_hand"], 12)
        self.assertEqual(rows[0]["free"], 12)
        self.assertIn("forecast", rows[0])
        self.assertEqual(rows[0]["warehouse"], "all")
        self.assertTrue(rows[0]["uom"])

    def test_get_product_stock_with_warehouse(self):
        warehouse = self.env["stock.warehouse"].search([], limit=1)
        text = f"{self.product.id},{warehouse.id}"
        rows = self.service.run_tool(1, "get_product_stock", text)["rows"]
        self.assertEqual(rows[0]["warehouse"], warehouse.name)
        self.assertEqual(rows[0]["on_hand"], 12)

    def test_get_product_stock_rejects_bad_input(self):
        for text in ("", "abc", "1;2", "1,2,3", "-5", "1,", "١٢"):
            self.assertEqual(
                self.service.run_tool(1, "get_product_stock", text)["rows"], [], text
            )

    def test_get_product_stock_rejects_foreign_warehouse_and_product(self):
        other, product = self._second_company_product()
        foreign_wh = self.env["stock.warehouse"].search(
            [("company_id", "=", other.id)], limit=1
        )
        text = f"{self.product.id},{foreign_wh.id}"
        self.assertEqual(self.service.run_tool(1, "get_product_stock", text)["rows"], [])
        self.assertEqual(
            self.service.run_tool(1, "get_product_stock", str(product.id))["rows"], []
        )

    def test_get_product_stock_denied_for_supplier(self):
        self.assertEqual(
            self.service.run_tool(2, "get_product_stock", "1")["error"], "denied"
        )

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


@tagged("post_install", "-at_install")
class TestQueryServiceLanguage(TransactionCase):
    """Background callers have no language; Spanish names must still match."""

    def setUp(self):
        super().setUp()
        self.env["res.lang"]._activate_lang("es_MX")
        product = self.env["product.product"].create(
            {"name": "Conference Chair", "is_storable": True, "type": "consu"}
        )
        product.product_tmpl_id.with_context(lang="es_MX").name = "Silla de conferencias"
        self.contact = self.env["rioclaro.telegram.contact"].create(
            {
                "name": "Lang tester",
                "telegram_user_id": "777",
                "role": "internal",
                "can_query_inventory": True,
                "company_id": self.env.company.id,
            }
        )

    def _names(self, lang):
        service = self.env["rioclaro.query.service"].with_context(lang=lang)
        result = service.run_tool("777", "stock_by_product", "silla")
        self.assertTrue(result["ok"])
        return [row["product"] for row in result["rows"]]

    def test_spanish_name_matches_without_caller_language(self):
        self.assertEqual(self._names(False), ["Silla de conferencias"])

    def test_spanish_name_matches_with_english_caller(self):
        self.assertEqual(self._names("en_US"), ["Silla de conferencias"])


@tagged("post_install", "-at_install")
class TestPublicWebsiteQueries(TransactionCase):
    def test_public_catalog_returns_only_published_product_content(self):
        public_product = self.env["product.template"].create(
            {
                "name": "Public Cloro Producto",
                "is_published": True,
                "website_description": "<p>Desinfectante para superficies.</p>",
                "list_price": 1234.0,
            }
        )
        self.env["product.template"].create(
            {
                "name": "Private Cloro Producto",
                "is_published": False,
                "website_description": "<p>Internal details.</p>",
            }
        )

        result = self.env["rioclaro.query.service"].run_public_tool(
            "public_catalog", "cloro"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["name"], public_product.name)
        self.assertEqual(
            result["rows"][0]["description"], "Desinfectante para superficies."
        )
        self.assertEqual(set(result["rows"][0]), {"name", "description"})

    def test_public_catalog_uses_description_shown_under_product_title(self):
        self.env["product.template"].create(
            {
                "name": "Limpia Pisos Test",
                "is_published": True,
                "description_ecommerce": "<p>Aroma a lavanda con poder antibacterial.</p>",
            }
        )

        result = self.env["rioclaro.query.service"].run_public_tool(
            "public_catalog", "lavanda"
        )

        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["name"], "Limpia Pisos Test")
        self.assertIn("lavanda", result["rows"][0]["description"])

    def test_public_catalog_general_list_returns_published_products(self):
        public_products = self.env["product.template"].create(
            [
                {"name": "Catalogo General Esponja", "is_published": True},
                {"name": "Catalogo General Trapeador", "is_published": True},
            ]
        )
        private_product = self.env["product.template"].create(
            {"name": "Catalogo General Privado", "is_published": False}
        )

        result = self.env["rioclaro.query.service"].run_public_tool(
            "public_catalog", "¿Qué productos tienen?"
        )

        self.assertTrue(result["ok"])
        result_names = {row["name"] for row in result["rows"]}
        self.assertTrue({product.name for product in public_products} <= result_names)
        self.assertNotIn(private_product.name, result_names)

    def test_public_tool_rejects_private_tool_names(self):
        result = self.env["rioclaro.query.service"].run_public_tool(
            "aggregate_sales", "30"
        )
        self.assertEqual(result, {"ok": False, "error": "unknown_tool"})

    def test_public_tool_rejects_oversized_search(self):
        result = self.env["rioclaro.query.service"].run_public_tool(
            "public_catalog", "x" * 81
        )
        self.assertEqual(result, {"ok": False, "error": "invalid_input"})

    def test_public_faq_returns_only_published_pages(self):
        website = self.env["website"].search([], limit=1)
        public_view = self.env["ir.ui.view"].create(
            {
                "name": "Public shipping FAQ test",
                "type": "qweb",
                "website_id": website.id,
                "arch_db": (
                    '<t t-name="website.rioclaro_public_shipping_faq">'
                    "<div>Los envíos llegan en días hábiles.</div></t>"
                ),
            }
        )
        self.env["website.page"].create(
            {
                "name": "Public shipping FAQ test",
                "url": "/test-public-shipping-faq",
                "view_id": public_view.id,
                "is_published": True,
            }
        )
        private_view = self.env["ir.ui.view"].create(
            {
                "name": "Private shipping FAQ test",
                "type": "qweb",
                "website_id": website.id,
                "arch_db": (
                    '<t t-name="website.rioclaro_private_shipping_faq">'
                    "<div>Información privada de envíos.</div></t>"
                ),
            }
        )
        self.env["website.page"].create(
            {
                "name": "Private shipping FAQ test",
                "url": "/test-private-shipping-faq",
                "view_id": private_view.id,
                "is_published": False,
            }
        )

        result = self.env["rioclaro.query.service"].run_public_tool(
            "public_faq", "envíos"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["title"], "Public shipping FAQ test")
        self.assertIn("Los envíos llegan", result["rows"][0]["content"])
