"""user_id_integer_to_bigint

Revision ID: a001_bigint_user_ids
Revises: eb9479bf9cf9
Create Date: 2026-03-07 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a001_bigint_user_ids'
down_revision: Union[str, None] = 'eb9479bf9cf9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # user_subscriptions
    op.alter_column('user_subscriptions', 'user_id', type_=sa.BigInteger())
    op.alter_column('user_subscriptions', 'referred_by', type_=sa.BigInteger())

    # user_wallet_tracking (FK references user_subscriptions.user_id)
    op.alter_column('user_wallet_tracking', 'user_id', type_=sa.BigInteger())

    # user_wallets
    op.alter_column('user_wallets', 'user_id', type_=sa.BigInteger())

    # user_api_creds
    op.alter_column('user_api_creds', 'user_id', type_=sa.BigInteger())

    # copy_trade_settings
    op.alter_column('copy_trade_settings', 'user_id', type_=sa.BigInteger())

    # copy_trade_history
    op.alter_column('copy_trade_history', 'user_id', type_=sa.BigInteger())

    # multibuy_alerts_sent
    op.alter_column('multibuy_alerts_sent', 'user_id', type_=sa.BigInteger())

    # referrals
    op.alter_column('referrals', 'referrer_id', type_=sa.BigInteger())
    op.alter_column('referrals', 'referee_id', type_=sa.BigInteger())


def downgrade():
    op.alter_column('user_subscriptions', 'user_id', type_=sa.Integer())
    op.alter_column('user_subscriptions', 'referred_by', type_=sa.Integer())
    op.alter_column('user_wallet_tracking', 'user_id', type_=sa.Integer())
    op.alter_column('user_wallets', 'user_id', type_=sa.Integer())
    op.alter_column('user_api_creds', 'user_id', type_=sa.Integer())
    op.alter_column('copy_trade_settings', 'user_id', type_=sa.Integer())
    op.alter_column('copy_trade_history', 'user_id', type_=sa.Integer())
    op.alter_column('multibuy_alerts_sent', 'user_id', type_=sa.Integer())
    op.alter_column('referrals', 'referrer_id', type_=sa.Integer())
    op.alter_column('referrals', 'referee_id', type_=sa.Integer())
