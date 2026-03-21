"""rename_is_tagwise_wallet_to_is_leaderboard_wallet

Revision ID: df1032b2c413
Revises: 
Create Date: 2026-02-23 12:46:39.848522

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'df1032b2c413'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.alter_column(
        'monitored_wallets',
        'is_tagwise_wallet',
        new_column_name='is_leaderboard_wallet'
    )

def downgrade():
    op.alter_column(
        'monitored_wallets',
        'is_leaderboard_wallet',
        new_column_name='is_tagwise_wallet'
    )
