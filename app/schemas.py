"""Input validation for supplier invoice capture."""

from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import InvoiceStatus


class InvoiceCreate(BaseModel):
    """Validated invoice fields submitted by the capture form."""

    model_config = ConfigDict(str_strip_whitespace=True)

    supplier_rfc: str = Field(min_length=1, max_length=20)
    folio: str = Field(min_length=1, max_length=100)
    invoice_date: date
    subtotal: Decimal
    taxes: Decimal
    total: Decimal

    @field_validator("subtotal", "taxes", "total", mode="before")
    @classmethod
    def validate_money(cls, value: object) -> Decimal:
        """Reject invalid, negative, or more-than-two-decimal MXN amounts."""
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError("El importe debe ser un número válido.") from None

        if not amount.is_finite():
            raise ValueError("El importe debe ser un número válido.")
        if amount < 0:
            raise ValueError("El importe no puede ser negativo.")
        if amount.as_tuple().exponent < -2:
            raise ValueError("El importe admite como máximo dos decimales.")
        if amount >= Decimal(10000000000):
            raise ValueError("El importe excede el máximo permitido.")
        return amount

    @model_validator(mode="after")
    def validate_total(self) -> "InvoiceCreate":
        """Require the invoice total to equal subtotal plus taxes."""
        if self.total != self.subtotal + self.taxes:
            raise ValueError("El total debe ser igual al subtotal más los impuestos.")
        return self


class InvoiceStatusUpdate(BaseModel):
    """Validated status change submitted by the review form."""

    status: InvoiceStatus
