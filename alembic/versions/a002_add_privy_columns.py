"""add privy wallet columns

Revision ID: a002_add_privy_columns
Revises: a001_bigint_user_ids
Create Date: 2026-03-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a002_add_privy_columns'
down_revision: Union[str, None] = 'a001_bigint_user_ids'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add Privy columns to user_wallets
    op.add_column('user_wallets', sa.Column('privy_user_id', sa.String(), nullable=True))
    op.add_column('user_wallets', sa.Column('privy_wallet_id', sa.String(), nullable=True))

    # Make encrypted_private_key nullable (Privy wallets don't store keys)
    # For SQLite, this is a no-op (SQLite doesn't enforce NOT NULL changes well)
    # For PostgreSQL, explicitly alter the column
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.alter_column('user_wallets', 'encrypted_private_key',
                        existing_type=sa.Text(),
                        nullable=True)


def downgrade() -> None:
    op.drop_column('user_wallets', 'privy_wallet_id')
    op.drop_column('user_wallets', 'privy_user_id')

    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.alter_column('user_wallets', 'encrypted_private_key',
                        existing_type=sa.Text(),
                        nullable=False)
