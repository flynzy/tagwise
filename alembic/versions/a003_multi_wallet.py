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

    if is_postgres:
        # Drop the existing UNIQUE constraint on user_id
        try:
            op.drop_constraint('user_wallets_user_id_key', 'user_wallets', type_='unique')
        except Exception:
            pass  # May have a different name
        # Composite unique: one wallet per (user_id, wallet_index)
        op.create_unique_constraint('uq_user_wallet_index', 'user_wallets', ['user_id', 'wallet_index'])

    # ── user_api_creds: tie creds to a specific wallet, not just user ──
    op.add_column('user_api_creds', sa.Column('wallet_id', sa.BigInteger(), nullable=True))

    if is_postgres:
        # Populate wallet_id by joining to the wallet row we just migrated
        op.execute("""
            UPDATE user_api_creds c
            SET wallet_id = w.id
            FROM user_wallets w
            WHERE w.user_id = c.user_id
        """)
        # Drop old user_id unique constraint
        try:
            op.drop_constraint('user_api_creds_user_id_key', 'user_api_creds', type_='unique')
        except Exception:
            pass
        # Unique per wallet (nullable rows skipped by partial index behaviour)
        op.create_index('ix_user_api_creds_wallet_id', 'user_api_creds', ['wallet_id'], unique=True)
    else:
        # SQLite
        op.execute("""
            UPDATE user_api_creds
            SET wallet_id = (
                SELECT id FROM user_wallets
                WHERE user_wallets.user_id = user_api_creds.user_id
                LIMIT 1
            )
        """)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    if is_postgres:
        try:
            op.drop_index('ix_user_api_creds_wallet_id', table_name='user_api_creds')
        except Exception:
            pass
        op.create_unique_constraint('user_api_creds_user_id_key', 'user_api_creds', ['user_id'])
        try:
            op.drop_constraint('uq_user_wallet_index', 'user_wallets', type_='unique')
        except Exception:
            pass
        op.create_unique_constraint('user_wallets_user_id_key', 'user_wallets', ['user_id'])

    op.drop_column('user_api_creds', 'wallet_id')
    op.drop_column('user_wallets', 'wallet_name')
    op.drop_column('user_wallets', 'is_active')
    op.drop_column('user_wallets', 'wallet_index')
