from odoo import fields, models


class SupplierInvoiceIntakeLine(models.Model):
    _name = "rioclaro.invoice.intake.line"
    _description = "Extracted Supplier Invoice Line"
    _order = "id"

    intake_id = fields.Many2one(
        "rioclaro.invoice.intake",
        required=True,
        ondelete="cascade",
        index=True,
    )
    description = fields.Char(required=True)
    quantity = fields.Float()
    unit_price = fields.Monetary(currency_field="currency_id")
    amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(
        related="intake_id.currency_id",
        store=True,
        readonly=True,
    )
    confidence = fields.Float()