"""invoice drafts and voids: an owner's text makes a draft (not sent yet), and
an amount change voids the invoice and a new one replaces it

Revision ID: 0033_invoice_drafts_voids
Revises: 0032_demo_day_hours
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_invoice_drafts_voids"
down_revision = "0032_demo_day_hours"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoices") as batch:
        batch.alter_column("sent_at", existing_type=sa.DateTime(timezone=True), nullable=True)
        batch.add_column(sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("voided_by", sa.String(length=320), nullable=True))
        batch.add_column(sa.Column("replaces", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM invoices WHERE sent_at IS NULL")
    with op.batch_alter_table("invoices") as batch:
        batch.drop_column("replaces")
        batch.drop_column("voided_by")
        batch.drop_column("voided_at")
        batch.alter_column("sent_at", existing_type=sa.DateTime(timezone=True), nullable=False)
