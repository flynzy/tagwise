-- Migration: Convert all TIMESTAMP WITHOUT TIME ZONE columns to TIMESTAMP WITH TIME ZONE
-- Existing naive timestamps will be interpreted as UTC by PostgreSQL
-- Run this BEFORE deploying the updated database.py

-- tracked_wallets
ALTER TABLE tracked_wallets ALTER COLUMN tracked_since TYPE TIMESTAMPTZ USING tracked_since AT TIME ZONE 'UTC';
ALTER TABLE tracked_wallets ALTER COLUMN last_checked TYPE TIMESTAMPTZ USING last_checked AT TIME ZONE 'UTC';
ALTER TABLE tracked_wallets ALTER COLUMN last_trade_time TYPE TIMESTAMPTZ USING last_trade_time AT TIME ZONE 'UTC';
ALTER TABLE tracked_wallets ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
ALTER TABLE tracked_wallets ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at AT TIME ZONE 'UTC';

-- user_subscriptions
ALTER TABLE user_subscriptions ALTER COLUMN subscription_started_at TYPE TIMESTAMPTZ USING subscription_started_at AT TIME ZONE 'UTC';
ALTER TABLE user_subscriptions ALTER COLUMN subscription_expires_at TYPE TIMESTAMPTZ USING subscription_expires_at AT TIME ZONE 'UTC';
ALTER TABLE user_subscriptions ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
ALTER TABLE user_subscriptions ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at AT TIME ZONE 'UTC';

-- trade_events
ALTER TABLE trade_events ALTER COLUMN timestamp TYPE TIMESTAMPTZ USING timestamp AT TIME ZONE 'UTC';
ALTER TABLE trade_events ALTER COLUMN detected_at TYPE TIMESTAMPTZ USING detected_at AT TIME ZONE 'UTC';

-- sent_notifications
ALTER TABLE sent_notifications ALTER COLUMN sent_at TYPE TIMESTAMPTZ USING sent_at AT TIME ZONE 'UTC';

-- sent_trades
ALTER TABLE sent_trades ALTER COLUMN sent_at TYPE TIMESTAMPTZ USING sent_at AT TIME ZONE 'UTC';

-- user_settings
ALTER TABLE user_settings ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
ALTER TABLE user_settings ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at AT TIME ZONE 'UTC';

-- tagwise_wallets
ALTER TABLE tagwise_wallets ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
ALTER TABLE tagwise_wallets ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at AT TIME ZONE 'UTC';

-- paymento_transactions
ALTER TABLE paymento_transactions ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at AT TIME ZONE 'UTC';

-- payment_links
ALTER TABLE payment_links ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';

-- multibuy_records
ALTER TABLE multibuy_records ALTER COLUMN timestamp TYPE TIMESTAMPTZ USING timestamp AT TIME ZONE 'UTC';

-- multibuy_notifications
ALTER TABLE multibuy_notifications ALTER COLUMN sent_at TYPE TIMESTAMPTZ USING sent_at AT TIME ZONE 'UTC';

-- copy_trade_orders
ALTER TABLE copy_trade_orders ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';

-- referrals (new table from referral feature)
ALTER TABLE referrals ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
ALTER TABLE referrals ALTER COLUMN converted_at TYPE TIMESTAMPTZ USING converted_at AT TIME ZONE 'UTC';
