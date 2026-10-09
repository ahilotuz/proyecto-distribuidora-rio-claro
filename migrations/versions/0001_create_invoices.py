"""Create invoice table.

Revision ID: 0001_create_invoices
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_create_invoices"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the initial invoices table and its integrity constraints."""
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("supplier_rfc", sa.String(length=20), nullable=False),
        sa.Column("folio", sa.String(length=100), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("subtotal", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("taxes", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("total", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDIENTE_REVISION",
                "VALIDADA",
                "RECHAZADA",
                name="invoice_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("subtotal >= 0", name="ck_invoice_subtotal_nonnegative"),
        sa.CheckConstraint("taxes >= 0", name="ck_invoice_taxes_nonnegative"),
        sa.CheckConstraint("total >= 0", name="ck_invoice_total_nonnegative"),
        sa.CheckConstraint(
            "total = subtotal + taxes",
            name="ck_invoice_total_matches_components",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "supplier_rfc",
            "folio",
            name="uq_invoice_supplier_folio",
        ),
    )


def downgrade() -> None:
    """Remove the invoices table."""
    op.drop_table("invoices")
