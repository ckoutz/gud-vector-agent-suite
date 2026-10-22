"""customers e-mail optional: an owner may add a customer by text with only a
phone number; that customer has no portal sign-in until an e-mail is added

Revision ID: 0034_customers_email_optional
Revises: 0033_invoice_drafts_voids
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_customers_email_optional"
down_revision = "0033_invoice_drafts_voids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("customers") as batch:
        batch.alter_column("email", existing_type=sa.String(length=320), nullable=True)


def downgrade() -> None:
    # Unique per business, and never deliverable (.invalid is reserved).
    op.execute(
        "UPDATE customers SET email = 'no-email+' || CAST(id AS VARCHAR) || '@example.invalid' "
        "WHERE email IS NULL"
    )
    with op.batch_alter_table("customers") as batch:
        batch.alter_column("email", existing_type=sa.String(length=320), nullable=False)
