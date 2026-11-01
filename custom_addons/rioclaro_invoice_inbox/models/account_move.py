from odoo import _, models


class AccountMove(models.Model):
    """Extends account.move to release intake records when their draft bill is deleted."""

    _inherit = "account.move"

    def unlink(self):
        """Delete the bills and send any linked intake back to 'In Review'.

        Returns:
            bool: Result of the standard unlink.
        """
        intakes = self.env["rioclaro.invoice.intake"].sudo().search(
            [("invoice_id", "in", self.ids), ("state", "=", "invoiced")]
        )
        result = super().unlink()
        for intake in intakes:
            intake.write({"state": "in_review"})
            intake.message_post(
                body=_("Vendor bill deleted; the intake can be invoiced again.")
            )
        return result
