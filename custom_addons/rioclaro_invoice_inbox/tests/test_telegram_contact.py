"""Tests for the Telegram contact ACL."""

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, ValidationError
from odoo.addons.rioclaro_invoice_inbox.models.telegram_contact import PERMISSION_FIELDS
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestTelegramContact(TransactionCase):
    def setUp(self):
        super().setUp()
        self.contacts = self.env["rioclaro.telegram.contact"]
        self.partner = self.env["res.partner"].create({"name": "Proveedor SA"})

    def _create(self, telegram_user_id: str, role: str, **extra):
        return self.contacts.create(
            {
                "name": f"Contact {telegram_user_id}",
                "telegram_user_id": telegram_user_id,
                "role": role,
                **extra,
            }
        )

    def test_unknown_telegram_user_is_denied(self):
        self.assertFalse(self.contacts.is_allowed(404, "can_query_inventory"))

    def test_unknown_permission_name_is_denied(self):
        self._create("100", "internal")
        self.assertFalse(self.contacts.is_allowed(100, "can_do_anything"))

    def test_internal_role_gets_every_permission(self):
        self._create("101", "internal")
        for permission in (
            "can_query_inventory",
            "can_query_sales",
            "can_submit_vendor_invoice",
            "can_issue_customer_invoice",
        ):
            self.assertTrue(self.contacts.is_allowed(101, permission))

    def test_supplier_role_gets_only_its_defaults(self):
        self._create("102", "supplier", partner_id=self.partner.id)
        self.assertTrue(self.contacts.is_allowed(102, "can_submit_vendor_invoice"))
        self.assertTrue(self.contacts.is_allowed("102", "can_query_purchases"))
        self.assertFalse(self.contacts.is_allowed(102, "can_query_inventory"))
        self.assertFalse(self.contacts.is_allowed(102, "can_request_quotation"))

    def test_customer_role_gets_only_its_defaults(self):
        self._create("103", "customer", partner_id=self.partner.id)
        self.assertTrue(self.contacts.is_allowed(103, "can_check_order_status"))
        self.assertTrue(self.contacts.is_allowed(103, "can_request_quotation"))
        self.assertFalse(self.contacts.is_allowed(103, "can_submit_vendor_invoice"))
        self.assertFalse(self.contacts.is_allowed(103, "can_query_inventory"))

    def test_permission_can_be_revoked_per_contact(self):
        contact = self._create("104", "internal")
        contact.can_query_sales = False
        self.assertFalse(self.contacts.is_allowed(104, "can_query_sales"))
        self.assertTrue(self.contacts.is_allowed(104, "can_query_purchases"))

    def test_explicit_permissions_override_role_defaults(self):
        self._create(
            "105",
            "supplier",
            partner_id=self.partner.id,
            can_submit_vendor_invoice=False,
        )
        self.assertFalse(self.contacts.is_allowed(105, "can_submit_vendor_invoice"))

    def test_inactive_contact_is_denied(self):
        contact = self._create("106", "internal")
        contact.active = False
        self.assertFalse(self.contacts.is_allowed(106, "can_query_inventory"))

    def test_public_contacts_have_no_private_permissions_or_companies(self):
        contact = self._create("111", "public")

        self.assertFalse(any(contact[field] for field in PERMISSION_FIELDS))
        self.assertFalse(contact.allowed_company_ids)
        self.assertFalse(self.contacts.is_allowed(111, "can_query_sales"))

        with self.assertRaises(ValidationError):
            contact.can_query_sales = True

        company_contact = self._create("115", "public")
        with self.assertRaises(ValidationError):
            company_contact.allowed_company_ids = [
                (6, 0, [self.env.company.id])
            ]

    def test_internal_contact_can_be_assigned_multiple_companies(self):
        contact = self._create("114", "internal")
        other_company = self.env["res.company"].create({"name": "Second Company"})

        contact.allowed_company_ids = [
            (6, 0, [self.env.company.id, other_company.id])
        ]

        self.assertEqual(
            set(contact.allowed_company_ids.ids),
            {self.env.company.id, other_company.id},
        )

    def test_public_contact_is_created_once_and_inactive_records_are_found(self):
        contact, created = self.contacts.get_or_create_public_contact(
            112,
            "  Telegram   User ",
            "telegram:555:1",
        )

        self.assertTrue(created)
        self.assertEqual(contact.name, "Telegram User")
        self.assertEqual(contact.role, "public")
        self.assertEqual(contact.onboarding_state, "awaiting_name")
        self.assertEqual(contact.onboarding_last_message_id, "telegram:555:1")
        self.assertFalse(any(contact[field] for field in PERMISSION_FIELDS))
        self.assertFalse(contact.allowed_company_ids)

        contact.active = False
        existing, created_again = self.contacts.get_or_create_public_contact(
            112,
            "Different name",
            "telegram:555:2",
        )

        self.assertFalse(created_again)
        self.assertEqual(existing, contact)
        self.assertFalse(existing.active)
        self.assertEqual(
            self.contacts.with_context(active_test=False).search_count(
                [("telegram_user_id", "=", "112")]
            ),
            1,
        )

    def test_invalid_optional_email_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._create("113", "public", email="not-an-email")

    def test_telegram_id_must_be_numeric(self):
        with self.assertRaises(ValidationError):
            self._create("@someone", "internal")

    def test_external_roles_require_a_partner(self):
        with self.assertRaises(ValidationError):
            self._create("107", "supplier")
        with self.assertRaises(ValidationError):
            self._create("108", "customer")

    def test_telegram_id_is_unique(self):
        self._create("109", "internal")
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self._create("109", "internal")

    def test_regular_user_cannot_read_contacts(self):
        self._create("110", "internal")
        user = self.env["res.users"].create(
            {
                "name": "Accountant",
                "login": "acl_accountant",
                "group_ids": [(6, 0, [self.env.ref("account.group_account_invoice").id])],
            }
        )
        with self.assertRaises(AccessError):
            self.contacts.with_user(user).search([])
