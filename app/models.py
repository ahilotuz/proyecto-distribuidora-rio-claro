"""Persistent invoice model and review states."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InvoiceStatus(str, Enum):
    """Human review state of a supplier invoice."""

    PENDIENTE_REVISION = "PENDIENTE_REVISION"
    VALIDADA = "VALIDADA"
    RECHAZADA = "RECHAZADA"


class Invoice(Base):
    """Supplier invoice captured for manual review."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("supplier_rfc", "folio", name="uq_invoice_supplier_folio"),
        CheckConstraint(
            "(document_key IS NULL AND document_original_name IS NULL "
            "AND document_media_type IS NULL AND document_size_bytes IS NULL "
            "AND document_sha256 IS NULL) OR "
            "(document_key IS NOT NULL AND document_original_name IS NOT NULL "
            "AND document_media_type IS NOT NULL AND document_size_bytes IS NOT NULL "
            "AND document_sha256 IS NOT NULL)",
            name="ck_invoice_document_metadata_complete",
        ),
        CheckConstraint("subtotal >= 0", name="ck_invoice_subtotal_nonnegative"),
        CheckConstraint("taxes >= 0", name="ck_invoice_taxes_nonnegative"),
        CheckConstraint("total >= 0", name="ck_invoice_total_nonnegative"),
        CheckConstraint(
            "total = subtotal + taxes",
            name="ck_invoice_total_matches_components",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_rfc: Mapped[str] = mapped_column(String(20), nullable=False)
    folio: Mapped[str] = mapped_column(String(100), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    taxes: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        SqlEnum(
            InvoiceStatus,
            name="invoice_status",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
        default=InvoiceStatus.PENDIENTE_REVISION,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    document_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    document_original_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    document_media_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    document_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
