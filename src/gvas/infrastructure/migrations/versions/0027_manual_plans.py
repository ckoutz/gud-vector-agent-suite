"""manual plans: paid from / paid through dates, one manual plan per quote,
no customer needed for a manual plan (quotes sent to a phone number only)

Revision ID: 0027_manual_plans
Revises: 0026_payment_status_before
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_manual_plans"
down_revision = "0026_payment_status_before"
branch_labels = None
depends_on = None

MANUAL = sa.text("provider = 'manual'")
CUSTOMER_UNLESS_MANUAL = "ck_quote_subscriptions_customer_unless_manual"


def upgrade() -> None:
    op.add_column("quote_subscriptions", sa.Column("paid_from", sa.Date(), nullable=True))
    op.add_column("quote_subscriptions", sa.Column("paid_through", sa.Date(), nullable=True))
    op.create_index(
        "uq_quote_subscriptions_one_manual_plan",
        "quote_subscriptions",
        ["business_id", "quote_id"],
        unique=True,
        postgresql_where=MANUAL,
        sqlite_where=MANUAL,
    )
    op.alter_column("quote_subscriptions", "customer_id", nullable=True)
    op.create_check_constraint(
        CUSTOMER_UNLESS_MANUAL,
        "quote_subscriptions",
        "customer_id IS NOT NULL OR provider = 'manual'",
    )


def downgrade() -> None:
    op.drop_constraint(CUSTOMER_UNLESS_MANUAL, "quote_subscriptions", type_="check")
    op.execute("DELETE FROM quote_subscriptions WHERE customer_id IS NULL")
    op.alter_column("quote_subscriptions", "customer_id", nullable=False)
    op.drop_index("uq_quote_subscriptions_one_manual_plan", table_name="quote_subscriptions")
    op.drop_column("quote_subscriptions", "paid_through")
    op.drop_column("quote_subscriptions", "paid_from")
