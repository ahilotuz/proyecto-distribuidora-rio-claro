"""Add original invoice document metadata.

Revision ID: 0002_add_invoice_documents
Revises: 0001_create_invoices
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_invoice_documents"
down_revision: str | None = "0001_create_invoices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable metadata to preserve invoices created before this feature."""
    op.add_column("invoices", sa.Column("document_key", sa.String(length=80)))
    op.add_column(
        "invoices",
        sa.Column("document_original_name", sa.String(length=255)),
    )
    op.add_column(
        "invoices",
        sa.Column("document_media_type", sa.String(length=100)),
    )
    op.add_column(
        "invoices",
        sa.Column("document_size_bytes", sa.BigInteger()),
    )
    op.add_column("invoices", sa.Column("document_sha256", sa.String(length=64)))
    op.create_check_constraint(
        "ck_invoice_document_metadata_complete",
        "invoices",
        "(document_key IS NULL AND document_original_name IS NULL "
        "AND document_media_type IS NULL AND document_size_bytes IS NULL "
        "AND document_sha256 IS NULL) OR "
        "(document_key IS NOT NULL AND document_original_name IS NOT NULL "
        "AND document_media_type IS NOT NULL AND document_size_bytes IS NOT NULL "
        "AND document_sha256 IS NOT NULL)",
    )


def downgrade() -> None:
    """Remove invoice document metadata."""
    op.drop_constraint(
        "ck_invoice_document_metadata_complete",
        "invoices",
        type_="check",
    )
    op.drop_column("invoices", "document_sha256")
    op.drop_column("invoices", "document_size_bytes")
    op.drop_column("invoices", "document_media_type")
    op.drop_column("invoices", "document_original_name")
    op.drop_column("invoices", "document_key")
