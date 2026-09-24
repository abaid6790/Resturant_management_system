"""stock_movements: negative balance is app-enforced, not a DB constraint

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-22
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    # Going negative is a deliberate, permission-gated override
    # (settings.inventory.allow_negative_stock), enforced in app/services/inventory.py.
    # Raw SQL: op.drop_constraint() re-applies the project's naming convention to a name
    # that is already fully formed, which double-prefixes it.
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_balance_nonneg")


def downgrade():
    op.execute(
        "ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_balance_nonneg "
        "CHECK (new_balance >= 0)"
    )
