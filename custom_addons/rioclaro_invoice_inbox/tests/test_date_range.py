"""Tests for the bounded date-range parser."""

from datetime import date, datetime

from odoo.tests import BaseCase, tagged

from odoo.addons.rioclaro_invoice_inbox.models.date_range import (
    MAX_DAYS,
    parse_date_range,
)

TODAY = date(2026, 10, 21)


@tagged("post_install", "-at_install")
class TestDateRange(BaseCase):
    def test_empty_defaults_to_last_30_days_inclusive(self):
        period = parse_date_range("", TODAY)
        self.assertEqual((period.start, period.end), (date(2026, 9, 22), TODAY))

    def test_days_argument(self):
        self.assertEqual(parse_date_range("1", TODAY).start, TODAY)
        self.assertEqual(parse_date_range(str(MAX_DAYS), TODAY).start, date(2025, 10, 21))

    def test_explicit_range(self):
        period = parse_date_range("2026-01-01, 2026-01-31", TODAY)
        self.assertEqual((period.start, period.end), (date(2026, 1, 1), date(2026, 1, 31)))

    def test_utc_boundaries_use_mexico_city_offset(self):
        period = parse_date_range("2026-01-01,2026-01-01", TODAY)
        self.assertEqual(period.start_utc, datetime(2026, 1, 1, 6, 0))
        self.assertEqual(period.end_utc_exclusive, datetime(2026, 1, 2, 6, 0))

    def test_invalid_values_raise(self):
        for text in (
            "0",
            "367",
            "abc",
            "2026-01-01",
            "2026-02-01,2026-01-01",
            "2026-01-01,2026-12-31",
            "2024-01-01,2026-01-01",
            "20260101,20260102",
            "2026-01-01,2026-01-02,2026-01-03",
            "١٢",
        ):
            with self.assertRaises(ValueError, msg=text):
                parse_date_range(text, TODAY)
