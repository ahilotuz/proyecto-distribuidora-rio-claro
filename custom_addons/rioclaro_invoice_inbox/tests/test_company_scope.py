"""Tests for explicit company selection in private Telegram queries."""

from odoo.addons.rioclaro_invoice_inbox.models.company_scope import (
    resolve_company_selection,
)
from odoo.tests import BaseCase, tagged


@tagged("post_install", "-at_install")
class TestCompanyScope(BaseCase):
    companies = [(1, "Río Claro S.A."), (2, "Sucursal Norte")]

    def test_company_names_ignore_accents_and_punctuation(self):
        status, company_ids = resolve_company_selection(
            "ventas de Rio Claro SA esta semana", self.companies, [1]
        )

        self.assertEqual(status, "ok")
        self.assertEqual(company_ids, (1,))

    def test_multiple_companies_must_all_be_allowed(self):
        status, company_ids = resolve_company_selection(
            "ventas de Río Claro S.A. y Sucursal Norte",
            self.companies,
            [1, 2],
        )

        self.assertEqual(status, "ok")
        self.assertEqual(company_ids, (1, 2))

    def test_missing_company_requires_selection(self):
        self.assertEqual(
            resolve_company_selection("ventas esta semana", self.companies, [1]),
            ("required", ()),
        )

    def test_unauthorized_company_is_rejected(self):
        self.assertEqual(
            resolve_company_selection(
                "ventas de Sucursal Norte", self.companies, [1]
            ),
            ("forbidden", ()),
        )

    def test_company_name_must_match_as_a_whole_phrase(self):
        self.assertEqual(
            resolve_company_selection("Rio", self.companies, [1]),
            ("required", ()),
        )
