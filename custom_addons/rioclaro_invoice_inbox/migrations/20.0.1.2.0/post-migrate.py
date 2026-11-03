"""Migrate Telegram contacts to explicit multi-company access."""

from typing import Any


def migrate(cr: Any, version: str | None) -> None:
    cr.execute(
        """
        INSERT INTO rioclaro_telegram_contact_company_rel (contact_id, company_id)
        SELECT id, company_id
          FROM rioclaro_telegram_contact
         WHERE role != 'public' AND company_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    cr.execute(
        """
        DELETE FROM rioclaro_telegram_contact_company_rel
         WHERE contact_id IN (
             SELECT id FROM rioclaro_telegram_contact WHERE role = 'public'
         )
        """
    )
    cr.execute(
        """
        UPDATE rioclaro_telegram_contact
           SET company_id = NULL,
               can_query_inventory = FALSE,
               can_query_purchases = FALSE,
               can_query_sales = FALSE,
               can_query_billing = FALSE,
               can_query_margins = FALSE,
               can_check_order_status = FALSE,
               can_request_quotation = FALSE,
               can_submit_vendor_invoice = FALSE,
               can_issue_customer_invoice = FALSE
         WHERE role = 'public'
        """
    )
