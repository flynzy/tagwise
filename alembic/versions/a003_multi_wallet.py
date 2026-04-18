"""Add multi-wallet support

Revision ID: a003_multi_wallet
Revises: a002_add_privy_columns
Create Date: 2026-04-01
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a003_multi_wallet'
down_revision: Union[str, None] = 'a002_add_privy_columns'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    # ── user_wallets: add multi-wallet columns ──────────────────────
    op.add_column('user_wallets', sa.Column('wallet_index', sa.BigInteger(), nullable=True))
    op.add_column('user_wallets', sa.Column('is_active', sa.Boolean(), nullable=True))
    op.add_column('user_wallets', sa.Column('wallet_name', sa.String(), nullable=True))

    # Set sensible defaults for existing rows
    op.execute("UPDATE user_wallets SET wallet_index = 1, is_active = true WHERE wallet_index IS NULL")

    # ── user_api_creds: tie creds to a specific wallet ──────────────
    op.add_column('user_api_creds', sa.Column('wallet_id', sa.BigInteger(), nullable=True))

    if is_postgres:
        # Populate wallet_id from the existing single wallet row
        op.execute("""
            UPDATE user_api_creds c
            SET wallet_id = w.id
            FROM user_wallets w
            WHERE w.user_id = c.user_id
        """)


def downgrade() -> None:
    op.drop_column('user_api_creds', 'wallet_id')
    op.drop_column('user_wallets', 'wallet_name')
    op.drop_column('user_wallets', 'is_active')
    op.drop_column('user_wallets', 'wallet_index')
