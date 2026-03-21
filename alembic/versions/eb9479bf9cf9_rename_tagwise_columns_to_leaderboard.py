"""rename_tagwise_columns_to_leaderboard

Revision ID: eb9479bf9cf9
Revises: df1032b2c413
Create Date: 2026-02-23 12:50:23.498223

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eb9479bf9cf9'
down_revision: Union[str, None] = 'df1032b2c413'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # monitored_wallets — SKIP, is_leaderboard_wallet already exists
    
    # user_subscriptions — still needs renaming
    op.alter_column(
        'user_subscriptions',
        'track_tagwise_wallets',
        new_column_name='track_leaderboard_wallets'
    )


def downgrade():
    op.alter_column(
        'user_subscriptions',
        'track_leaderboard_wallets',
        new_column_name='track_tagwise_wallets'
    )

