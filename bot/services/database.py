# database.py (Async Refactored)

from sqlalchemy import select, update, delete, func, text 
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship, selectinload
from sqlalchemy import Column, BigInteger, String, Float, Boolean, DateTime, JSON, ForeignKey, Text, Index
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple
import enum
import logging
import json
import hashlib
import base64
import os
from cryptography.fernet import Fernet

from bot.config import Config, TierLimits

class Base(DeclarativeBase):
    pass

logger = logging.getLogger(__name__)


class WalletType(enum.Enum):
    """Type of wallet tracking"""
    CUSTOM = "custom"
    LEADERBOARD = "leaderboard"
    TAGWISE = "tagwise"


class SubscriptionTier(enum.Enum):
    """User subscription tiers"""
    FREE = "FREE"
    PRO = "PRO"


class UserWalletTracking(Base):
    """Association table for tracking which users follow which wallets"""
    __tablename__ = 'user_wallet_tracking'
    
    user_id = Column(BigInteger, ForeignKey('user_subscriptions.user_id'), primary_key=True)
    wallet_address = Column(String, ForeignKey('monitored_wallets.address'), primary_key=True)
    tracked_since = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    custom_name = Column(String, nullable=True)
    wallet_type = Column(String, default=WalletType.CUSTOM.value)
    
    user = relationship("UserSubscription", back_populates="wallet_trackings")
    wallet = relationship("MonitoredWallet", back_populates="user_trackings")


class MonitoredWallet(Base):
    __tablename__ = 'monitored_wallets'
    
    id = Column(BigInteger, primary_key=True)
    address = Column(String, unique=True, nullable=False, index=True)
    name = Column(String)
    
    # Leaderboard info
    leaderboard_rank = Column(BigInteger, nullable=True)
    x_username = Column(String, nullable=True)
    verified_badge = Column(Boolean, default=False)
    
    # Performance metrics
    roi_7d = Column(Float, default=0.0)
    roi_30d = Column(Float, default=0.0)
    roi_90d = Column(Float, default=0.0)
    win_rate = Column(Float, default=0.0)
    win_rate_7d = Column(Float, default=0.0)
    total_volume_7d = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)
    total_trades = Column(BigInteger, default=0)
    avg_trade_size = Column(Float, default=0.0)
    
    # Status
    is_active = Column(Boolean, default=True)
    is_leaderboard_wallet = Column(Boolean, default=False)
    last_checked = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_trade_time = Column(DateTime(timezone=True))
    
    extra_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    user_trackings = relationship('UserWalletTracking', back_populates='wallet', cascade='all, delete-orphan')


class UserSubscription(Base):
    __tablename__ = 'user_subscriptions'
    
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False, unique=True)
    username = Column(String)
    
    # Subscription tier
    tier = Column(String, default=SubscriptionTier.FREE.value)
    subscription_started_at = Column(DateTime(timezone=True), nullable=True)
    subscription_expires_at = Column(DateTime(timezone=True), nullable=True)
    subscription_type = Column(String, nullable=True)
    
    # Payment info
    payment_method = Column(String, nullable=True)
    last_payment_tx = Column(String, nullable=True)
    last_payment_amount = Column(Float, nullable=True)
    
    # Track all Tagwise wallets automatically
    track_leaderboard_wallets = Column(Boolean, default=False)
    
    # Notification settings
    notifications_enabled = Column(Boolean, default=True)
    min_trade_value = Column(Float, default=100.0)
    min_confidence_score = Column(BigInteger, default=1)
    
    # ✅ NEW: Multi-buy alert settings
    multibuy_enabled = Column(Boolean, default=False)
    multibuy_min_wallets = Column(BigInteger, default=2)
    multibuy_min_amount = Column(Float, default=0.0)
    
    extra_data = Column(JSON)
    
    # Referral system
    referral_code = Column(String, unique=True, nullable=True, index=True)  # This user's unique referral code
    referred_by = Column(BigInteger, nullable=True)  # user_id of who referred them
    
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    wallet_trackings = relationship('UserWalletTracking', back_populates='user', cascade='all, delete-orphan')


class Trade(Base):
    __tablename__ = 'trades'
    
    id = Column(BigInteger, primary_key=True)
    wallet_address = Column(String, nullable=False, index=True)
    
    market_id = Column(String)
    market_title = Column(String)
    market_slug = Column(String)
    market_category = Column(String, nullable=True)
    outcome = Column(String)
    side = Column(String)
    
    shares = Column(Float)
    usdc_value = Column(Float)
    price = Column(Float)
    
    # Confidence scoring
    confidence_score = Column(BigInteger, nullable=True)
    confidence_factors = Column(JSON, nullable=True)
    
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    detected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    extra_data = Column(JSON)


class Alert(Base):
    __tablename__ = 'alerts'
    
    id = Column(BigInteger, primary_key=True)
    trade_id = Column(BigInteger, ForeignKey('trades.id'))
    wallet_address = Column(String, nullable=False)
    
    channel_id = Column(String, nullable=False)
    message_id = Column(BigInteger)
    
    sent_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    extra_data = Column(JSON)


class SentTrade(Base):
    __tablename__ = 'sent_trades'
    
    id = Column(BigInteger, primary_key=True)
    transaction_hash = Column(String, unique=True, nullable=False, index=True)
    wallet_address = Column(String, nullable=False)
    sent_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ==================== TRADING MODELS ====================

class UserWallet(Base):
    """User trading wallets for copy trading with Safe support"""
    __tablename__ = 'user_wallets'
    
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    
    # EOA (Externally Owned Account) - the signing key
    address = Column(String, nullable=False)
    encrypted_private_key = Column(Text, nullable=True)  # nullable for Privy wallets

    # Safe wallet address (derived from EOA, used for trading)
    safe_address = Column(String, nullable=True)

    # Legacy field - kept for backwards compatibility, now derived automatically
    proxy_address = Column(String, nullable=True)

    # Privy wallet infrastructure (TEE-backed keys)
    privy_user_id = Column(String, nullable=True)
    privy_wallet_id = Column(String, nullable=True)
    
    wallet_type = Column(String, default='created')  # 'created' or 'imported'
    
    # Safe deployment status
    safe_deployed = Column(Boolean, default=False)
    allowances_set = Column(Boolean, default=False)
    
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class UserApiCreds(Base):
    """Stored API credentials for Polymarket CLOB"""
    __tablename__ = 'user_api_creds'
    
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    api_key = Column(String, nullable=False)
    api_secret = Column(String, nullable=False)
    api_passphrase = Column(String, nullable=False)
    signature_type = Column(BigInteger, default=0)  # 0=EOA, 1=Magic, 2=Safe/Proxy
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CopyTradeSettings(Base):
    """Copy trading settings per user"""
    __tablename__ = 'copy_trade_settings'
    
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    enabled = Column(Boolean, default=False)
    mode = Column(String, default='dry_run')  # 'dry_run' or 'live'
    
    # Legacy fields (kept for backward compatibility)
    max_trade_size = Column(Float, default=50.0)
    portfolio_percentage = Column(Float, default=10.0)
    
    # NEW: Buy settings
    buy_amount_type = Column(String, default='percentage')  # 'fixed' or 'percentage'
    buy_amount_value = Column(Float, default=10.0)  # $ amount or % of portfolio
    
    # NEW: Sell settings  
    sell_amount_type = Column(String, default='percentage_holdings')  # 'fixed' or 'percentage_holdings'
    sell_amount_value = Column(Float, default=100.0)  # $ amount or % of holdings
    
    min_price = Column(Float, default=0.05)
    max_price = Column(Float, default=0.95)
    min_target_trade_value = Column(Float, default=100.0)
    copy_buys = Column(Boolean, default=True)
    copy_sells = Column(Boolean, default=True)
    
    # Multi-buy only mode (PRO feature)
    multi_buy_only = Column(Boolean, default=False)
    multibuythreshold = Column(BigInteger, default=2)       # ← ADD
    multibuysellmode = Column(String, default='any')
    multibuywindow = Column(BigInteger, default=1)
    
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CopyTradeHistory(Base):
    """History of copy trades executed"""
    __tablename__ = 'copy_trade_history'
    
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    source_wallet = Column(String, nullable=False)
    original_trade = Column(JSON, nullable=True)
    copy_result = Column(JSON, nullable=True)
    success = Column(Boolean, default=False)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MultiBuyRecord(Base):
    """Records of recent buys for multi-buy detection"""
    __tablename__ = 'multibuy_records'
    
    id = Column(BigInteger, primary_key=True)
    market_id = Column(String, nullable=False, index=True)
    market_title = Column(String, nullable=True)
    outcome = Column(String, nullable=False)
    token_id = Column(String, nullable=True)
    wallet_address = Column(String, nullable=False, index=True)
    price = Column(Float, default=0)
    usdc_size = Column(Float, default=0)
    trade_hash = Column(String, unique=True, nullable=False)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class MultiBuyAlertSent(Base):
    """Track which multi-buy alerts have been sent to prevent duplicates"""
    __tablename__ = 'multibuy_alerts_sent'
    
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    market_id = Column(String, nullable=False)
    outcome = Column(String, nullable=False)
    wallet_combo_hash = Column(String, nullable=False)
    wallet_count = Column(BigInteger, default=2)
    sent_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class MultiBuyProcessed(Base):
    """Track processed multi-buy combinations to prevent duplicates"""
    __tablename__ = 'multibuy_processed'
    
    id = Column(BigInteger, primary_key=True)
    market_id = Column(String, nullable=False, index=True)
    outcome = Column(String, nullable=False)
    wallet_fingerprint = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    
    # Composite unique constraint
    __table_args__ = (
        Index('idx_multibuy_unique', 'market_id', 'outcome', 'wallet_fingerprint', unique=True),
    )


class Referral(Base):
    """Tracks referral relationships and rewards"""
    __tablename__ = 'referrals'
    
    id = Column(BigInteger, primary_key=True)
    referrer_id = Column(BigInteger, nullable=False, index=True)  # User who shared the link
    referee_id = Column(BigInteger, unique=True, nullable=False, index=True)  # User who signed up
    referral_code = Column(String, nullable=False)  # Code used
    status = Column(String, default='registered')  # registered, subscribed (referee bought PRO)
    reward_days_granted = Column(BigInteger, default=0)  # Days of PRO credited to referrer
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    converted_at = Column(DateTime(timezone=True), nullable=True)  # When referee subscribed


# ==================== DATABASE CLASS ====================

class Database:
    """Async database wrapper class for easy interaction"""
    
    def __init__(self, database_url: str = None):
        # Convert sync URL to async URL if needed
        if database_url is None:
            database_url = Config.DATABASE_URL
            
        # Convert postgresql:// to postgresql+asyncpg://
        if database_url.startswith('postgresql://'):
            database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://')
        elif database_url.startswith('sqlite://'):
            database_url = database_url.replace('sqlite://', 'sqlite+aiosqlite://')
        
        self.database_url = database_url
        self.engine = None
        self.async_session_maker = None
    
    async def connect(self):
        """Initialize async engine and session maker"""
        self.engine = create_async_engine(
            self.database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        
        self.async_session_maker = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        
        # Create tables if they don't exist
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        logger.info("✅ Async database initialized")
    
    async def close(self):
        """Close database connections"""
        if self.engine:
            await self.engine.dispose()
            logger.info("✅ Database connections closed")
    
    def get_session(self) -> AsyncSession:
        """Get a new async database session (use as async context manager)"""
        return self.async_session_maker()
    
    def _get_cipher(self):
        """Get Fernet cipher for API credential encryption."""
        key = os.getenv('WALLET_ENCRYPTION_KEY', '')
        if not key:
            raise ValueError("WALLET_ENCRYPTION_KEY required for API credential encryption")
        # Derive a Fernet-compatible key
        key_bytes = hashlib.sha256(key.encode()).digest()
        fernet_key = base64.urlsafe_b64encode(key_bytes)
        return Fernet(fernet_key)

    def _encrypt_value(self, value: str) -> str:
        """Encrypt a string value."""
        cipher = self._get_cipher()
        return cipher.encrypt(value.encode()).decode()

    def _decrypt_value(self, value: str) -> str:
        """Decrypt a string value."""
        cipher = self._get_cipher()
        return cipher.decrypt(value.encode()).decode()

    # ==================== TIER MANAGEMENT ====================
    
    async def get_user_tier(self, user_id: int) -> str:
        """Get user's current subscription tier"""
        async with self.get_session() as session:
            try:
                stmt = select(UserSubscription).where(UserSubscription.user_id == user_id)
                result = await session.execute(stmt)
                user = result.scalars().first()
                
                if not user:
                    return SubscriptionTier.FREE.value
                
                # Check if PRO subscription has expired
                if user.tier == SubscriptionTier.PRO.value:
                    if user.subscription_expires_at and user.subscription_expires_at < datetime.now(timezone.utc):
                        user.tier = SubscriptionTier.FREE.value
                        await session.commit()
                        logger.info(f"User {user_id} PRO subscription expired, downgraded to FREE")
                        return SubscriptionTier.FREE.value
                
                return user.tier
                
            except Exception as e:
                logger.error(f"Error getting user tier: {e}")
                return SubscriptionTier.FREE.value
    
    async def is_pro(self, user_id: int) -> bool:
        """Check if user has active PRO subscription"""
        tier = await self.get_user_tier(user_id)
        return tier == SubscriptionTier.PRO.value
    
    async def get_tier_limits(self, user_id: int) -> Dict:
        """Get feature limits for user's tier"""
        is_pro = await self.is_pro(user_id)
        
        if is_pro:
            return {
                'tier': 'PRO',
                'max_custom_wallets': TierLimits.PRO_MAX_CUSTOM_WALLETS,
                'max_leaderboard_traders': TierLimits.PRO_MAX_LEADERBOARD_TRADERS,
                'confidence_scores': TierLimits.PRO_CONFIDENCE_SCORES,
                'leaderboard_filters': TierLimits.PRO_LEADERBOARD_FILTERS,
            }
        else:
            return {
                'tier': 'FREE',
                'max_custom_wallets': TierLimits.FREE_MAX_CUSTOM_WALLETS,
                'max_leaderboard_traders': TierLimits.FREE_MAX_TAGWISE_TRADERS,
                'confidence_scores': TierLimits.FREE_CONFIDENCE_SCORES,
                'leaderboard_filters': TierLimits.FREE_LEADERBOARD_FILTERS,
            }
    
    async def get_user_wallet_counts(self, user_id: int) -> Dict[str, int]:
        """Get count of wallets user is tracking by type"""
        async with self.get_session() as session:
            try:
                # Count custom wallets
                custom_stmt = select(func.count()).select_from(UserWalletTracking).where(
                    UserWalletTracking.user_id == user_id,
                    UserWalletTracking.wallet_type == WalletType.CUSTOM.value
                )
                custom_result = await session.execute(custom_stmt)
                custom_count = custom_result.scalar()
                
                # Count leaderboard wallets
                leaderboard_stmt = select(func.count()).select_from(UserWalletTracking).where(
                    UserWalletTracking.user_id == user_id,
                    UserWalletTracking.wallet_type == WalletType.TAGWISE.value
                )
                leaderboard_result = await session.execute(leaderboard_stmt)
                leaderboard_count = leaderboard_result.scalar()
                
                return {
                    'custom': custom_count,
                    'leaderboard': leaderboard_count,
                    'total': custom_count + leaderboard_count
                }
                
            except Exception as e:
                logger.error(f"Error getting wallet counts: {e}")
                return {'custom': 0, 'leaderboard': 0, 'total': 0}
    
    async def can_add_wallet(self, user_id: int, wallet_type: str) -> Tuple[bool, str]:
        """Check if user can add another wallet based on tier limits."""
        limits = await self.get_tier_limits(user_id)
        counts = await self.get_user_wallet_counts(user_id)
        
        if wallet_type == WalletType.CUSTOM.value:
            if counts['custom'] >= limits['max_custom_wallets']:
                return (
                    False, 
                    f"You've reached the {limits['tier']} limit of {limits['max_custom_wallets']} custom wallets.\n\n"
                    f"🔥 Upgrade to PRO for unlimited wallets!"
                )
        elif wallet_type == WalletType.TAGWISE.value:
            if counts['leaderboard'] >= limits['max_leaderboard_traders']:
                return (
                    False,
                    f"You've reached the {limits['tier']} limit of {limits['max_leaderboard_traders']} top traders.\n\n"
                    f"🔥 Upgrade to PRO to track all {TierLimits.PRO_MAX_TAGWISE_TRADERS}+ top traders!"
                )
        
        return (True, "")
    
    async def upgrade_to_pro(
        self, 
        user_id: int, 
        subscription_type: str = 'monthly',
        payment_method: str = None,
        payment_tx: str = None,
        payment_amount: float = None
    ) -> bool:
        """Upgrade user to PRO tier"""
        async with self.get_session() as session:
            try:
                stmt = select(UserSubscription).where(UserSubscription.user_id == user_id)
                result = await session.execute(stmt)
                user = result.scalars().first()
                
                if not user:
                    user = UserSubscription(user_id=user_id)
                    session.add(user)
                
                now = datetime.now(timezone.utc)
                
                if subscription_type == 'annual':
                    expires_at = now + timedelta(days=365)
                else:
                    expires_at = now + timedelta(days=30)
                
                if user.tier == SubscriptionTier.PRO.value and user.subscription_expires_at:
                    if user.subscription_expires_at > now:
                        if subscription_type == 'annual':
                            expires_at = user.subscription_expires_at + timedelta(days=365)
                        else:
                            expires_at = user.subscription_expires_at + timedelta(days=30)
                
                user.tier = SubscriptionTier.PRO.value
                user.subscription_started_at = now
                user.subscription_expires_at = expires_at
                user.subscription_type = subscription_type
                user.payment_method = payment_method
                user.last_payment_tx = payment_tx
                user.last_payment_amount = payment_amount
                
                await session.commit()
                logger.info(f"✅ User {user_id} upgraded to PRO (expires: {expires_at})")
                
                # ✅ Grant referral reward to whoever referred this user
                try:
                    referrer_id = await self.process_referral_reward(user_id)
                    if referrer_id:
                        logger.info(f"Referral reward granted to user {referrer_id} (referee {user_id} upgraded)")
                except Exception as ref_err:
                    logger.warning(f"Could not process referral reward for user {user_id}: {ref_err}")
                
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error upgrading user to PRO: {e}")
                return False

    async def get_or_create_referral_code(self, user_id: int) -> str:
        """Get existing referral code or generate a new one for the user."""
        async with self.get_session() as session:
            try:
                stmt = select(UserSubscription).where(UserSubscription.user_id == user_id)
                result = await session.execute(stmt)
                user = result.scalars().first()
                
                if not user:
                    user = UserSubscription(user_id=user_id)
                    session.add(user)
                
                if user.referral_code:
                    return user.referral_code
                
                # Generate unique code: 8 char alphanumeric
                import secrets
                code = secrets.token_urlsafe(6)  # ~8 chars
                user.referral_code = code
                await session.commit()
                return code
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error creating referral code: {e}")
                return None

    async def record_referral(self, referrer_code: str, referee_id: int) -> bool:
        """Record that a new user was referred. Called from /start deep link."""
        async with self.get_session() as session:
            try:
                # Find the referrer by their code
                stmt = select(UserSubscription).where(UserSubscription.referral_code == referrer_code)
                result = await session.execute(stmt)
                referrer = result.scalars().first()
                
                if not referrer:
                    logger.warning(f"Invalid referral code: {referrer_code}")
                    return False
                
                # Don't allow self-referral
                if referrer.user_id == referee_id:
                    return False
                
                # Check if referee already has a referral record
                stmt = select(Referral).where(Referral.referee_id == referee_id)
                result = await session.execute(stmt)
                existing = result.scalars().first()
                if existing:
                    return False  # Already referred
                
                # Create referral record
                referral = Referral(
                    referrer_id=referrer.user_id,
                    referee_id=referee_id,
                    referral_code=referrer_code,
                    status='registered'
                )
                session.add(referral)
                
                # Mark referee as referred
                referee_stmt = select(UserSubscription).where(UserSubscription.user_id == referee_id)
                referee_result = await session.execute(referee_stmt)
                referee_user = referee_result.scalars().first()
                if not referee_user:
                    referee_user = UserSubscription(user_id=referee_id)
                    session.add(referee_user)
                referee_user.referred_by = referrer.user_id
                
                await session.commit()
                logger.info(f"Referral recorded: {referrer.user_id} -> {referee_id} (code: {referrer_code})")
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error recording referral: {e}")
                return False

    async def process_referral_reward(self, referee_id: int, reward_days: int = 7) -> Optional[int]:
        """
        When a referee subscribes to PRO, grant reward days to the referrer.
        Returns the referrer's user_id if reward was granted, None otherwise.
        """
        async with self.get_session() as session:
            try:
                # Find the referral record for this referee
                stmt = select(Referral).where(
                    Referral.referee_id == referee_id,
                    Referral.status == 'registered'
                )
                result = await session.execute(stmt)
                referral = result.scalars().first()
                
                if not referral:
                    return None  # No pending referral
                
                # Mark referral as converted
                referral.status = 'subscribed'
                referral.converted_at = datetime.now(timezone.utc)
                referral.reward_days_granted = reward_days
                
                # Add PRO days to the referrer
                referrer_stmt = select(UserSubscription).where(
                    UserSubscription.user_id == referral.referrer_id
                )
                referrer_result = await session.execute(referrer_stmt)
                referrer = referrer_result.scalars().first()
                
                if referrer:
                    now = datetime.now(timezone.utc)
                    if referrer.tier == SubscriptionTier.PRO.value and referrer.subscription_expires_at and referrer.subscription_expires_at > now:
                        # Extend existing PRO
                        referrer.subscription_expires_at += timedelta(days=reward_days)
                    else:
                        # Grant new PRO
                        referrer.tier = SubscriptionTier.PRO.value
                        referrer.subscription_started_at = now
                        referrer.subscription_expires_at = now + timedelta(days=reward_days)
                        referrer.subscription_type = 'referral'
                
                await session.commit()
                logger.info(f"Referral reward: {reward_days} days PRO to user {referral.referrer_id} (from referee {referee_id})")
                return referral.referrer_id
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error processing referral reward: {e}")
                return None

    async def get_referral_stats(self, user_id: int) -> dict:
        """Get referral statistics for a user."""
        async with self.get_session() as session:
            try:
                # Get user's referral code
                code = await self.get_or_create_referral_code(user_id)
                
                # Count total referrals
                stmt = select(Referral).where(Referral.referrer_id == user_id)
                result = await session.execute(stmt)
                referrals = result.scalars().all()
                
                total_referrals = len(referrals)
                converted = len([r for r in referrals if r.status == 'subscribed'])
                total_days_earned = sum(r.reward_days_granted for r in referrals)
                
                return {
                    'referral_code': code,
                    'total_referrals': total_referrals,
                    'converted': converted,
                    'total_days_earned': total_days_earned,
                }
                
            except Exception as e:
                logger.error(f"Error getting referral stats: {e}")
                return {
                    'referral_code': None,
                    'total_referrals': 0,
                    'converted': 0,
                    'total_days_earned': 0,
                }

    async def apply_referee_trial(self, user_id: int, trial_days: int = 3) -> bool:
        """Give a new referred user a free PRO trial."""
        async with self.get_session() as session:
            try:
                stmt = select(UserSubscription).where(UserSubscription.user_id == user_id)
                result = await session.execute(stmt)
                user = result.scalars().first()
                
                if not user:
                    return False
                
                # Only give trial if user is not already PRO
                if user.tier == SubscriptionTier.PRO.value:
                    return False
                
                now = datetime.now(timezone.utc)
                user.tier = SubscriptionTier.PRO.value
                user.subscription_started_at = now
                user.subscription_expires_at = now + timedelta(days=trial_days)
                user.subscription_type = 'referral_trial'
                
                await session.commit()
                logger.info(f"Applied {trial_days}-day PRO trial to referred user {user_id}")
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error applying referral trial: {e}")
                return False

    async def get_subscription_info(self, user_id: int) -> Dict:
        """Get detailed subscription information"""
        async with self.get_session() as session:
            try:
                stmt = select(UserSubscription).where(UserSubscription.user_id == user_id)
                result = await session.execute(stmt)
                user = result.scalars().first()
                
                if not user:
                    return {
                        'tier': SubscriptionTier.FREE.value,
                        'is_pro': False,
                        'expires_at': None,
                        'days_remaining': None,
                        'subscription_type': None,
                    }
                
                is_pro = user.tier == SubscriptionTier.PRO.value
                days_remaining = None
                
                if is_pro and user.subscription_expires_at:
                    if user.subscription_expires_at < datetime.now(timezone.utc):
                        is_pro = False
                    else:
                        days_remaining = (user.subscription_expires_at - datetime.now(timezone.utc)).days
                
                return {
                    'tier': user.tier if is_pro else SubscriptionTier.FREE.value,
                    'is_pro': is_pro,
                    'expires_at': user.subscription_expires_at,
                    'days_remaining': days_remaining,
                    'subscription_type': user.subscription_type,
                    'started_at': user.subscription_started_at,
                }
                
            except Exception as e:
                logger.error(f"Error getting subscription info: {e}")
                return {'tier': SubscriptionTier.FREE.value, 'is_pro': False}


    # ==================== MULTI-BUY ALERT SETTINGS ====================
    
    async def get_multibuy_settings(self, user_id: int) -> dict:
        """
        Get multi-buy notification settings for a user.
        
        Returns:
            dict with keys:
                - enabled: bool
                - min_wallets: int (minimum number of wallets for alert)
                - min_amount: float (minimum buy amount per wallet)
        """
        async with self.get_session() as session:
            try:
                stmt = select(UserSubscription).where(UserSubscription.user_id == user_id)
                result = await session.execute(stmt)
                user = result.scalars().first()
                
                if not user:
                    # Return default settings
                    return {
                        'enabled': False,
                        'min_wallets': 2,
                        'min_amount': 0.0
                    }
                
                return {
                    'enabled': user.multibuy_enabled or False,
                    'min_wallets': user.multibuy_min_wallets or 2,
                    'min_amount': float(user.multibuy_min_amount or 0.0)
                }
                
            except Exception as e:
                logger.error(f"Error getting multibuy settings: {e}")
                return {
                    'enabled': False,
                    'min_wallets': 2,
                    'min_amount': 0.0
                }
    
    async def get_users_with_multibuy_alerts(self) -> list[int]:
        """
        Get all user IDs that have multi-buy alerts enabled.
        
        Returns:
            List of telegram user IDs
        """
        async with self.get_session() as session:
            try:
                stmt = select(UserSubscription.user_id).where(
                    UserSubscription.multibuy_enabled == True
                )
                result = await session.execute(stmt)
                rows = result.all()
                return [row[0] for row in rows]
                
            except Exception as e:
                logger.error(f"Error getting users with multibuy alerts: {e}")
                return []
    
    async def update_multibuy_settings(
        self,
        user_id: int,
        enabled: bool = None,
        min_wallets: int = None,
        min_amount: float = None
    ) -> bool:
        """Update multi-buy alert settings for a user"""
        async with self.get_session() as session:
            try:
                stmt = select(UserSubscription).where(UserSubscription.user_id == user_id)
                result = await session.execute(stmt)
                user = result.scalars().first()
                
                if not user:
                    user = UserSubscription(user_id=user_id)
                    session.add(user)
                    await session.flush()
                
                if enabled is not None:
                    user.multibuy_enabled = enabled
                if min_wallets is not None:
                    user.multibuy_min_wallets = min_wallets
                if min_amount is not None:
                    user.multibuy_min_amount = min_amount
                
                user.updated_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info(f"✅ Updated multibuy settings for user {user_id}")
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating multibuy settings: {e}")
                return False
    
    # ==================== WALLET TRACKING ====================
    
    async def get_wallet_stats_for_confidence(self, wallet_address: str) -> Dict:
        """Get wallet stats needed for confidence scoring"""
        async with self.get_session() as session:
            try:
                wallet_address = wallet_address.lower()
                
                stmt = select(MonitoredWallet).where(MonitoredWallet.address == wallet_address)
                result = await session.execute(stmt)
                wallet = result.scalars().first()
                
                if not wallet:
                    return {}
                
                return {
                    'win_rate': wallet.win_rate or 0,
                    'leaderboard_rank': wallet.leaderboard_rank,
                    'total_pnl': wallet.total_pnl or 0,
                    'verified_badge': wallet.verified_badge or False,
                    'avg_trade_size': wallet.avg_trade_size or 0,
                    'total_trades': wallet.total_trades or 0,
                }
                
            except Exception as e:
                logger.error(f"Error getting wallet stats for confidence: {e}")
                return {}
    
    async def update_wallet_win_rate(self, wallet_address: str, win_rate: float, avg_trade_size: float = None):
        """Update wallet's win rate and average trade size"""
        async with self.get_session() as session:
            try:
                stmt = select(MonitoredWallet).where(MonitoredWallet.address == wallet_address.lower())
                result = await session.execute(stmt)
                wallet = result.scalars().first()
                
                if wallet:
                    wallet.win_rate = win_rate
                    if avg_trade_size is not None:
                        wallet.avg_trade_size = avg_trade_size
                    await session.commit()
                    
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating wallet win rate: {e}")
    
    async def add_tracked_wallet(
        self, 
        user_id: int, 
        wallet_address: str, 
        custom_name: str = None,
        wallet_type: str = WalletType.CUSTOM.value,
        leaderboard_info: dict = None
    ) -> bool:
        """Add a wallet to user's tracking list."""
        async with self.get_session() as session:
            try:
                wallet_address = wallet_address.lower()
                
                # Get or create user
                stmt = select(UserSubscription).where(UserSubscription.user_id == user_id)
                result = await session.execute(stmt)
                user = result.scalars().first()
                
                if not user:
                    user = UserSubscription(user_id=user_id)
                    session.add(user)
                    await session.flush()
                
                # Get or create wallet
                stmt = select(MonitoredWallet).where(MonitoredWallet.address == wallet_address)
                result = await session.execute(stmt)
                wallet = result.scalars().first()
                
                if not wallet:
                    wallet = MonitoredWallet(
                        address=wallet_address,
                        is_leaderboard_wallet=(wallet_type == WalletType.TAGWISE.value),
                        last_checked=None
                    )
                    session.add(wallet)
                    await session.flush()
                
                # Update leaderboard info
                if leaderboard_info:
                    username = leaderboard_info.get('username')
                    if username and len(username) > 40 and '-' in username:
                        username = None
                    wallet.name = username or wallet.name
                    
                    rank_value = leaderboard_info.get('rank')
                    if rank_value is not None:
                        try:
                            wallet.leaderboard_rank = int(rank_value)
                        except (ValueError, TypeError):
                            wallet.leaderboard_rank = None
                    
                    x_username = leaderboard_info.get('x_username')
                    wallet.x_username = x_username if x_username else None
                    
                    wallet.verified_badge = leaderboard_info.get('verified', False)
                    wallet.total_pnl = float(leaderboard_info.get('pnl', 0) or 0)
                    wallet.total_volume_7d = float(leaderboard_info.get('volume', 0) or 0)
                    wallet.is_leaderboard_wallet = (wallet_type == WalletType.TAGWISE.value)
                    await session.flush()
                
                # Check if tracking already exists
                stmt = select(UserWalletTracking).where(
                    UserWalletTracking.user_id == user_id,
                    UserWalletTracking.wallet_address == wallet_address
                )
                result = await session.execute(stmt)
                tracking = result.scalars().first()
                
                if not tracking:
                    tracking = UserWalletTracking(
                        user_id=user_id,
                        wallet_address=wallet_address,
                        custom_name=custom_name,
                        wallet_type=wallet_type
                    )
                    session.add(tracking)
                    await session.commit()
                    logger.info(f"✅ User {user_id} now tracking {wallet_address} (type: {wallet_type})")
                    return True
                else:
                    if custom_name:
                        tracking.custom_name = custom_name
                    tracking.wallet_type = wallet_type
                    await session.commit()
                    return False
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding tracked wallet: {e}", exc_info=True)
                return False

    async def get_tracked_wallets(self, user_id: int, wallet_type: str = None) -> List[Dict]:
        """Get all wallets tracked by a user."""
        async with self.get_session() as session:
            try:
                stmt = select(UserWalletTracking).where(UserWalletTracking.user_id == user_id)
                
                if wallet_type:
                    stmt = stmt.where(UserWalletTracking.wallet_type == wallet_type)
                
                result = await session.execute(stmt)
                trackings = result.scalars().all()
                
                wallets = []
                for tracking in trackings:
                    normalized_address = tracking.wallet_address.lower() if tracking.wallet_address else None
                    
                    if not normalized_address:
                        continue
                    
                    # Get wallet details
                    wallet_stmt = select(MonitoredWallet).where(MonitoredWallet.address == normalized_address)
                    wallet_result = await session.execute(wallet_stmt)
                    wallet = wallet_result.scalars().first()
                    
                    if wallet:
                        wallets.append({
                            'address': wallet.address,
                            'name': wallet.name,
                            'custom_name': tracking.custom_name,
                            'display_name': tracking.custom_name or wallet.name or f"{wallet.address[:6]}...{wallet.address[-4:]}",
                            'wallet_type': tracking.wallet_type,
                            'added_at': tracking.tracked_since,
                            'leaderboard_rank': wallet.leaderboard_rank,
                            'x_username': wallet.x_username,
                            'verified_badge': wallet.verified_badge,
                            'roi_7d': wallet.roi_7d,
                            'roi_30d': wallet.roi_30d,
                            'volume_7d': wallet.total_volume_7d,
                            'total_pnl': wallet.total_pnl,
                            'total_trades': wallet.total_trades,
                            'is_leaderboard_wallet': wallet.is_leaderboard_wallet
                        })
                    else:
                        logger.warning(f"Tracking exists for {normalized_address} but wallet not found")
                
                return wallets
                
            except Exception as e:
                logger.error(f"Error getting tracked wallets: {e}")
                return []

    async def remove_tracked_wallet(self, user_id: int, wallet_address: str) -> bool:
        """Remove a wallet from user's tracking list"""
        async with self.get_session() as session:
            try:
                wallet_address = wallet_address.lower()
                
                stmt = select(UserWalletTracking).where(
                    UserWalletTracking.user_id == user_id,
                    UserWalletTracking.wallet_address == wallet_address
                )
                result = await session.execute(stmt)
                tracking = result.scalars().first()
                
                if tracking:
                    await session.delete(tracking)
                    await session.commit()
                    logger.info(f"✅ User {user_id} stopped tracking {wallet_address}")
                    return True
                
                return False
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error removing tracked wallet: {e}")
                return False

    async def update_wallet_custom_name(self, user_id: int, wallet_address: str, custom_name: str) -> bool:
        """Update the custom name for a tracked wallet"""
        async with self.get_session() as session:
            try:
                wallet_address = wallet_address.lower()
                
                stmt = select(UserWalletTracking).where(
                    UserWalletTracking.user_id == user_id,
                    UserWalletTracking.wallet_address == wallet_address
                )
                result = await session.execute(stmt)
                tracking = result.scalars().first()
                
                if tracking:
                    tracking.custom_name = custom_name
                    await session.commit()
                    logger.info(f"✅ Updated wallet {wallet_address} name to '{custom_name}' for user {user_id}")
                    return True
                
                return False
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating wallet custom name: {e}")
                return False
    
    async def remove_all_leaderboard_wallets(self, user_id: int) -> int:
        """Remove all Custom wallets for a user. Returns count of removed."""
        async with self.get_session() as session:
            try:
                stmt = select(UserWalletTracking).where(
                    UserWalletTracking.user_id == user_id,
                    UserWalletTracking.wallet_type == WalletType.TAGWISE.value
                )
                result = await session.execute(stmt)
                trackings = result.scalars().all()
                
                count = len(trackings)
                for tracking in trackings:
                    await session.delete(tracking)
                
                await session.commit()
                logger.info(f"✅ Removed {count} custom wallets for user {user_id}")
                return count
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error removing Tagwise wallets: {e}")
                return 0
    
    async def remove_all_wallets(self, user_id: int) -> int:
        """Remove all wallets for a user. Returns count of removed."""
        async with self.get_session() as session:
            try:
                stmt = select(UserWalletTracking).where(UserWalletTracking.user_id == user_id)
                result = await session.execute(stmt)
                trackings = result.scalars().all()
                
                count = len(trackings)
                for tracking in trackings:
                    await session.delete(tracking)
                
                await session.commit()
                logger.info(f"✅ Removed all {count} wallets for user {user_id}")
                return count
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error removing all wallets: {e}")
                return 0
    
    async def set_leaderboard_subscription(self, user_id: int, enabled: bool) -> bool:
        """Enable or disable automatic Tagwise wallet tracking"""
        async with self.get_session() as session:
            try:
                stmt = select(UserSubscription).where(UserSubscription.user_id == user_id)
                result = await session.execute(stmt)
                user = result.scalars().first()
                
                if not user:
                    user = UserSubscription(user_id=user_id)
                    session.add(user)
                
                user.track_leaderboard_wallets = enabled
                await session.commit()
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error setting Tagwise subscription: {e}")
                return False
    
    async def get_all_tracked_wallets(self) -> List[Dict]:
        """Get all unique wallets being tracked by at least one user"""
        async with self.get_session() as session:
            try:
                # First, get distinct wallet addresses that have active subscriptions
                subq = (
                    select(UserWalletTracking.wallet_address)
                    .distinct()
                    .subquery()
                )
                
                # Then join to get the wallet details
                stmt = (
                    select(MonitoredWallet)
                    .join(subq, MonitoredWallet.address == subq.c.wallet_address)
                    .where(
                        (MonitoredWallet.is_active == True) | (MonitoredWallet.is_active == None)
                    )
                )
                
                result = await session.execute(stmt)
                wallets = result.scalars().all()
                
                logger.info(f"get_all_tracked_wallets returning {len(wallets)} wallets with active subscriptions")
                
                return [{
                    'address': w.address, 
                    'name': w.name,
                    'is_leaderboard_wallet': w.is_leaderboard_wallet,
                    'leaderboard_rank': w.leaderboard_rank
                } for w in wallets]
                
            except Exception as e:
                logger.error(f"Error getting all tracked wallets: {e}")
                return []

    async def get_leaderboard_wallets(self) -> List[Dict]:
        """Get all Tagwise curated wallets"""
        async with self.get_session() as session:
            try:
                stmt = select(MonitoredWallet).where(
                    MonitoredWallet.is_leaderboard_wallet == True,
                    MonitoredWallet.is_active == True
                ).order_by(MonitoredWallet.leaderboard_rank)
                
                result = await session.execute(stmt)
                wallets = result.scalars().all()
                
                return [{
                    'address': w.address, 
                    'name': w.name,
                    'leaderboard_rank': w.leaderboard_rank,
                    'x_username': w.x_username,
                    'verified_badge': w.verified_badge,
                    'total_pnl': w.total_pnl,
                    'volume_7d': w.total_volume_7d
                } for w in wallets]
                
            except Exception as e:
                logger.error(f"Error getting Tagwise wallets: {e}")
                return []
    
    async def update_leaderboard_wallets(self, traders: List[Dict]) -> int:
        """Update the list of Tagwise curated wallets from leaderboard data."""
        async with self.get_session() as session:
            try:
                count = 0
                for trader in traders:
                    address = trader.get('address', '').lower()
                    if not address:
                        continue
                    
                    stmt = select(MonitoredWallet).where(MonitoredWallet.address == address)
                    result = await session.execute(stmt)
                    wallet = result.scalars().first()
                    
                    if not wallet:
                        wallet = MonitoredWallet(address=address)
                        session.add(wallet)
                    
                    username = trader.get('username')
                    if username and len(username) > 40 and '-' in username:
                        username = None
                    
                    wallet.name = username or wallet.name
                    
                    rank_value = trader.get('rank')
                    if rank_value is not None:
                        try:
                            wallet.leaderboard_rank = int(rank_value)
                        except (ValueError, TypeError):
                            wallet.leaderboard_rank = None
                    
                    x_username = trader.get('x_username')
                    wallet.x_username = x_username if x_username else None
                    
                    wallet.verified_badge = trader.get('verified', False)
                    wallet.total_pnl = float(trader.get('pnl', 0) or 0)
                    wallet.total_volume_7d = float(trader.get('volume', 0) or 0)
                    wallet.is_leaderboard_wallet = True
                    wallet.is_active = True
                    wallet.updated_at = datetime.now(timezone.utc)
                    count += 1
                
                await session.commit()
                logger.info(f"✅ Updated {count} Tagwise wallets from leaderboard")
                return count
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating Tagwise wallets: {e}")
                return 0

    async def get_last_check_time(self, wallet_address: str) -> Optional[datetime]:
        """Get the last time a wallet was checked for trades"""
        async with self.get_session() as session:
            try:
                stmt = select(MonitoredWallet).where(MonitoredWallet.address == wallet_address.lower())
                result = await session.execute(stmt)
                wallet = result.scalars().first()
                
                if wallet and wallet.last_checked:
                    return wallet.last_checked
                
                return None
                
            except Exception as e:
                logger.error(f"Error getting last check time: {e}")
                return None

    async def update_last_check_time(self, wallet_address: str):
        """Update the last check time for a wallet"""
        async with self.get_session() as session:
            try:
                wallet_address = wallet_address.lower()
                
                stmt = select(MonitoredWallet).where(MonitoredWallet.address == wallet_address)
                result = await session.execute(stmt)
                wallet = result.scalars().first()
                
                if wallet:
                    wallet.last_checked = datetime.now(timezone.utc)
                else:
                    wallet = MonitoredWallet(
                        address=wallet_address,
                        last_checked=datetime.now(timezone.utc)
                    )
                    session.add(wallet)
                
                await session.commit()
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating last check time: {e}")
    
    async def get_users_tracking_wallet(self, wallet_address: str) -> List[int]:
        """Get all user IDs tracking a specific wallet"""
        async with self.get_session() as session:
            try:
                wallet_address = wallet_address.lower()
                
                stmt = select(UserWalletTracking).where(UserWalletTracking.wallet_address == wallet_address)
                result = await session.execute(stmt)
                trackings = result.scalars().all()
                
                return [t.user_id for t in trackings]
                
            except Exception as e:
                logger.error(f"Error getting users tracking wallet: {e}")
                return []
    
    async def get_wallet_display_name(self, user_id: int, wallet_address: str) -> str:
        """Get the display name for a wallet"""
        async with self.get_session() as session:
            try:
                wallet_address = wallet_address.lower()
                
                # Check for custom name
                stmt = select(UserWalletTracking).where(
                    UserWalletTracking.user_id == user_id,
                    UserWalletTracking.wallet_address == wallet_address
                )
                result = await session.execute(stmt)
                tracking = result.scalars().first()
                
                if tracking and tracking.custom_name:
                    return tracking.custom_name
                
                # Check wallet name
                stmt = select(MonitoredWallet).where(MonitoredWallet.address == wallet_address)
                result = await session.execute(stmt)
                wallet = result.scalars().first()
                
                if wallet and wallet.name:
                    return wallet.name
                
                return f"{wallet_address[:6]}...{wallet_address[-4:]}"
                
            except Exception as e:
                logger.error(f"Error getting wallet display name: {e}")
                return f"{wallet_address[:6]}...{wallet_address[-4:]}"
    
    async def update_wallet_stats(self, wallet_address: str, stats: Dict):
        """Update wallet statistics"""
        async with self.get_session() as session:
            try:
                stmt = select(MonitoredWallet).where(MonitoredWallet.address == wallet_address)
                result = await session.execute(stmt)
                wallet = result.scalars().first()
                
                if wallet:
                    wallet.name = stats.get('name') or wallet.name
                    wallet.roi_7d = stats.get('roi_7d', wallet.roi_7d)
                    wallet.roi_30d = stats.get('roi_30d', wallet.roi_30d)
                    wallet.total_volume_7d = stats.get('volume_7d', wallet.total_volume_7d)
                    wallet.total_trades = stats.get('total_trades', wallet.total_trades)
                    wallet.updated_at = datetime.now(timezone.utc)
                    await session.commit()
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating wallet stats: {e}")

    # ==================== TRADE TRACKING ====================

    async def is_trade_already_sent(self, transaction_hash: str) -> bool:
        """Check if a trade notification was already sent"""
        async with self.get_session() as session:
            try:
                stmt = select(SentTrade).where(SentTrade.transaction_hash == transaction_hash)
                result = await session.execute(stmt)
                exists = result.scalars().first() is not None
                return exists
            except Exception as e:
                logger.error(f"Error checking sent trade: {e}")
                return False

    async def mark_trade_as_sent(self, transaction_hash: str, wallet_address: str):
        """Mark a trade as sent to prevent duplicates (with proper duplicate handling)"""
        async with self.get_session() as session:
            try:
                # Use raw SQL with ON CONFLICT for PostgreSQL
                # This is the most efficient and safest approach
                query = text("""
                    INSERT INTO sent_trades (transaction_hash, wallet_address, sent_at)
                    VALUES (:tx_hash, :wallet, :sent_at)
                    ON CONFLICT (transaction_hash) DO NOTHING
                    RETURNING id
                """)
                
                result = await session.execute(
                    query,
                    {
                        "tx_hash": transaction_hash,
                        "wallet": wallet_address.lower(),
                        "sent_at": datetime.now(timezone.utc)
                    }
                )
                
                await session.commit()
                
                # Check if row was inserted (if RETURNING gives us a result)
                row = result.first()
                if row:
                    logger.debug(f"✅ Marked trade {transaction_hash[:10]}... as sent")
                else:
                    # Already existed - this is normal, not an error
                    logger.debug(f"⏭️  Trade {transaction_hash[:10]}... already marked (skipped)")
                    
            except IntegrityError as e:
                # This should rarely happen now with ON CONFLICT, but handle it gracefully
                await session.rollback()
                if 'duplicate key' in str(e).lower() or 'unique constraint' in str(e).lower():
                    logger.debug(f"⏭️  Trade {transaction_hash[:10]}... already exists (duplicate ignored)")
                else:
                    # Some other integrity error - this is worth logging
                    logger.error(f"Integrity error marking trade as sent: {e}")
                    
            except Exception as e:
                # Unexpected error
                await session.rollback()
                logger.error(f"Error marking trade as sent: {e}", exc_info=True)


    async def cleanup_old_sent_trades(self, days: int = 7):
        """Remove old sent trade records to prevent table bloat"""
        async with self.get_session() as session:
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                stmt = delete(SentTrade).where(SentTrade.sent_at < cutoff)
                await session.execute(stmt)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Error cleaning up sent trades: {e}")

    # ==================== TRADING WALLET METHODS ====================
    
    async def save_user_wallet(
        self,
        user_id: int,
        address: str,
        encrypted_private_key: str = None,
        safe_address: str = None,
        proxy_address: str = None,
        wallet_type: str = 'created',
        privy_user_id: str = None,
        privy_wallet_id: str = None,
    ) -> bool:
        """Save a user's trading wallet with Safe address"""
        async with self.get_session() as session:
            try:
                stmt = select(UserWallet).where(UserWallet.user_id == user_id)
                result = await session.execute(stmt)
                existing = result.scalars().first()

                effective_safe_address = safe_address or proxy_address

                if existing:
                    existing.address = address.lower()
                    existing.encrypted_private_key = encrypted_private_key
                    existing.safe_address = effective_safe_address.lower() if effective_safe_address else None
                    existing.proxy_address = effective_safe_address.lower() if effective_safe_address else None
                    existing.wallet_type = wallet_type
                    existing.privy_user_id = privy_user_id
                    existing.privy_wallet_id = privy_wallet_id
                    existing.updated_at = datetime.now(timezone.utc)
                else:
                    wallet = UserWallet(
                        user_id=user_id,
                        address=address.lower(),
                        encrypted_private_key=encrypted_private_key,
                        safe_address=effective_safe_address.lower() if effective_safe_address else None,
                        proxy_address=effective_safe_address.lower() if effective_safe_address else None,
                        wallet_type=wallet_type,
                        privy_user_id=privy_user_id,
                        privy_wallet_id=privy_wallet_id,
                    )
                    session.add(wallet)

                await session.commit()
                logger.info(f"Saved trading wallet for user {user_id}")
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error saving user wallet: {e}", exc_info=True)
                return False
    
    async def get_user_wallet(self, user_id: int, include_encrypted_key: bool = False) -> Optional[Dict]:
        """Get user's trading wallet info including Safe address"""
        async with self.get_session() as session:
            try:
                stmt = select(UserWallet).where(UserWallet.user_id == user_id)
                result = await session.execute(stmt)
                wallet = result.scalars().first()

                if not wallet:
                    return None

                result = {
                    'address': wallet.address,
                    'safe_address': wallet.safe_address or wallet.proxy_address,
                    'proxy_address': wallet.proxy_address,
                    'wallet_type': wallet.wallet_type,
                    'safe_deployed': wallet.safe_deployed,
                    'allowances_set': wallet.allowances_set,
                    'created_at': wallet.created_at,
                    'privy_user_id': wallet.privy_user_id,
                    'privy_wallet_id': wallet.privy_wallet_id,
                }

                if include_encrypted_key:
                    result['encrypted_private_key'] = wallet.encrypted_private_key

                return result

            except Exception as e:
                logger.error(f"Error getting user wallet: {e}")
                return None
    
    async def update_wallet_safe_address(self, user_id: int, safe_address: str) -> bool:
        """Update the Safe address for a trading wallet"""
        async with self.get_session() as session:
            try:
                stmt = select(UserWallet).where(UserWallet.user_id == user_id)
                result = await session.execute(stmt)
                wallet = result.scalars().first()
                
                if wallet:
                    wallet.safe_address = safe_address.lower() if safe_address else None
                    wallet.proxy_address = safe_address.lower() if safe_address else None
                    wallet.updated_at = datetime.now(timezone.utc)
                    await session.commit()
                    logger.info(f"✅ Updated Safe address for user {user_id}")
                    return True
                
                return False
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating Safe address: {e}")
                return False
    
    async def update_wallet_safe_deployed(self, user_id: int, is_deployed: bool) -> bool:
        """Update the safe_deployed flag for a trading wallet"""
        async with self.get_session() as session:
            try:
                stmt = select(UserWallet).where(UserWallet.user_id == user_id)
                result = await session.execute(stmt)
                wallet = result.scalars().first()
                
                if wallet:
                    wallet.safe_deployed = is_deployed
                    wallet.updated_at = datetime.now(timezone.utc)
                    await session.commit()
                    return True
                
                return False
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating Safe deployment status: {e}")
                return False
    
    async def delete_user_wallet(self, user_id: int) -> bool:
        """Delete a user's trading wallet and related data"""
        async with self.get_session() as session:
            try:
                # Delete wallet
                stmt = select(UserWallet).where(UserWallet.user_id == user_id)
                result = await session.execute(stmt)
                wallet = result.scalars().first()
                if wallet:
                    await session.delete(wallet)
                
                # Delete copy trade settings
                stmt = select(CopyTradeSettings).where(CopyTradeSettings.user_id == user_id)
                result = await session.execute(stmt)
                settings = result.scalars().first()
                if settings:
                    await session.delete(settings)
                
                # Delete API credentials
                stmt = select(UserApiCreds).where(UserApiCreds.user_id == user_id)
                result = await session.execute(stmt)
                creds = result.scalars().first()
                if creds:
                    await session.delete(creds)
                
                await session.commit()
                logger.info(f"✅ Deleted trading wallet and related data for user {user_id}")
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting user wallet: {e}")
                return False
    
    async def update_wallet_proxy(self, user_id: int, proxy_address: str) -> bool:
        """Update proxy address for a trading wallet (legacy method)"""
        return await self.update_wallet_safe_address(user_id, proxy_address)
    
    async def update_wallet_allowances_set(self, user_id: int, is_set: bool) -> bool:
        """Update allowances_set flag for a trading wallet"""
        async with self.get_session() as session:
            try:
                stmt = select(UserWallet).where(UserWallet.user_id == user_id)
                result = await session.execute(stmt)
                wallet = result.scalars().first()
                
                if wallet:
                    wallet.allowances_set = is_set
                    wallet.updated_at = datetime.now(timezone.utc)
                    await session.commit()
                    return True
                
                return False
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating wallet allowances: {e}")
                return False
    
    # ==================== COPY TRADE SETTINGS ====================
    
    async def get_copy_trade_settings(self, user_id: int) -> Optional[Dict]:
        """Get copy trade settings for a user"""
        async with self.get_session() as session:
            try:
                stmt = select(CopyTradeSettings).where(CopyTradeSettings.user_id == user_id)
                result = await session.execute(stmt)
                settings = result.scalars().first()
                
                if not settings:
                    return None
                
                return {
                    'enabled': settings.enabled,
                    'mode': settings.mode,
                    'max_trade_size': settings.max_trade_size,
                    'portfolio_percentage': settings.portfolio_percentage,
                    'buy_amount_type': settings.buy_amount_type or 'percentage',
                    'buy_amount_value': settings.buy_amount_value if settings.buy_amount_value is not None else 10.0,
                    'sell_amount_type': settings.sell_amount_type or 'percentage_holdings',
                    'sell_amount_value': settings.sell_amount_value if settings.sell_amount_value is not None else 100.0,
                    'min_price': settings.min_price,
                    'max_price': settings.max_price,
                    'min_target_trade_value': settings.min_target_trade_value,
                    'copy_buys': settings.copy_buys,
                    'copy_sells': settings.copy_sells,
                    'multi_buy_only': getattr(settings, 'multi_buy_only', False) or False,
                    'multibuythreshold': getattr(settings, 'multibuythreshold', 2) or 2,       # ← ADD
                    'multibuysellmode': getattr(settings, 'multibuysellmode', 'any') or 'any', # ← ADD
                    'multibuywindow': getattr(settings, 'multibuywindow', 1) or 1,
                }
                
            except Exception as e:
                logger.error(f"Error getting copy trade settings: {e}")
                return None

    async def save_copy_trade_settings(self, user_id: int, settings_dict: Dict) -> bool:
        """Save copy trade settings for a user"""
        async with self.get_session() as session:
            try:
                stmt = select(CopyTradeSettings).where(CopyTradeSettings.user_id == user_id)
                result = await session.execute(stmt)
                settings = result.scalars().first()
                
                if settings:
                    settings.enabled = settings_dict.get('enabled', False)
                    settings.mode = settings_dict.get('mode', 'dry_run')
                    settings.max_trade_size = settings_dict.get('max_trade_size', 50.0)
                    settings.portfolio_percentage = settings_dict.get('portfolio_percentage', 10.0)
                    settings.buy_amount_type = settings_dict.get('buy_amount_type', 'percentage')
                    settings.buy_amount_value = settings_dict.get('buy_amount_value', 10.0)
                    settings.sell_amount_type = settings_dict.get('sell_amount_type', 'percentage_holdings')
                    settings.sell_amount_value = settings_dict.get('sell_amount_value', 100.0)
                    settings.min_price = settings_dict.get('min_price', 0.05)
                    settings.max_price = settings_dict.get('max_price', 0.95)
                    settings.min_target_trade_value = settings_dict.get('min_target_trade_value', 100.0)
                    settings.copy_buys = settings_dict.get('copy_buys', True)
                    settings.copy_sells = settings_dict.get('copy_sells', True)
                    settings.multi_buy_only = settings_dict.get('multi_buy_only', False)
                    settings.multibuythreshold = settings_dict.get('multibuythreshold', 2)   # ← ADD
                    settings.multibuysellmode = settings_dict.get('multibuysellmode', 'any') # ← ADD
                    settings.multibuywindow = settings_dict.get('multibuywindow', 1) 
                    settings.updated_at = datetime.now(timezone.utc)
                else:
                    settings = CopyTradeSettings(
                        user_id=user_id,
                        enabled=settings_dict.get('enabled', False),
                        mode=settings_dict.get('mode', 'dry_run'),
                        max_trade_size=settings_dict.get('max_trade_size', 50.0),
                        portfolio_percentage=settings_dict.get('portfolio_percentage', 10.0),
                        buy_amount_type=settings_dict.get('buy_amount_type', 'percentage'),
                        buy_amount_value=settings_dict.get('buy_amount_value', 10.0),
                        sell_amount_type=settings_dict.get('sell_amount_type', 'percentage_holdings'),
                        sell_amount_value=settings_dict.get('sell_amount_value', 100.0),
                        min_price=settings_dict.get('min_price', 0.05),
                        max_price=settings_dict.get('max_price', 0.95),
                        min_target_trade_value=settings_dict.get('min_target_trade_value', 100.0),
                        copy_buys=settings_dict.get('copy_buys', True),
                        copy_sells=settings_dict.get('copy_sells', True),
                        multi_buy_only=settings_dict.get('multi_buy_only', False),
                        multibuythreshold=settings_dict.get('multibuythreshold', 2),
                        multibuysellmode=settings_dict.get('multibuysellmode', 'any'),
                        multibuywindow=settings_dict.get('multibuywindow', 1)
                    )
                    session.add(settings)
                
                await session.commit()
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error saving copy trade settings: {e}")
                return False

    async def get_users_with_copy_trading(self, source_wallet: str) -> List[Dict]:
        """Get all users who have copy trading enabled and are tracking a specific wallet"""
        async with self.get_session() as session:
            try:
                source_wallet = source_wallet.lower()
                
                logger.debug(f"🔍 Looking for users copy trading wallet: {source_wallet[:10]}...")
                
                # Build the query with joins
                stmt = (
                    select(CopyTradeSettings.user_id)
                    .join(UserWallet, CopyTradeSettings.user_id == UserWallet.user_id)
                    .join(UserWalletTracking, CopyTradeSettings.user_id == UserWalletTracking.user_id)
                    .where(
                        CopyTradeSettings.enabled == True,
                        UserWalletTracking.wallet_address == source_wallet,
                        UserWallet.allowances_set == True
                    )
                )
                
                result = await session.execute(stmt)
                user_ids = result.scalars().all()
                
                logger.debug(f"   Found {len(user_ids)} users to copy: {user_ids}")
                
                return [{'user_id': user_id} for user_id in user_ids]
                
            except Exception as e:
                logger.error(f"Error getting users with copy trading: {e}", exc_info=True)
                return []
    
    # ==================== COPY TRADE HISTORY ====================
    
    async def log_copy_trade(
        self,
        user_id: int,
        source_wallet: str,
        original_trade: Dict,
        copy_result: Dict
    ) -> bool:
        """Log a copy trade execution"""
        async with self.get_session() as session:
            try:
                history = CopyTradeHistory(
                    user_id=user_id,
                    source_wallet=source_wallet.lower(),
                    original_trade=original_trade,
                    copy_result=copy_result,
                    success=copy_result.get('success', False),
                    error_message=copy_result.get('error') if not copy_result.get('success') else None
                )
                session.add(history)
                await session.commit()
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error logging copy trade: {e}")
                return False
    
    async def get_copy_trade_history(self, user_id: int, limit: int = 20) -> List[Dict]:
        """Get copy trade history for a user"""
        async with self.get_session() as session:
            try:
                stmt = (
                    select(CopyTradeHistory)
                    .where(CopyTradeHistory.user_id == user_id)
                    .order_by(CopyTradeHistory.created_at.desc())
                    .limit(limit)
                )
                
                result = await session.execute(stmt)
                history = result.scalars().all()
                
                results = []
                for h in history:
                    original = h.original_trade or {}
                    result_data = h.copy_result or {}
                    
                    results.append({
                        'source_wallet': h.source_wallet,
                        'market': original.get('title', 'Unknown'),
                        'side': original.get('side', 'Unknown'),
                        'amount': result_data.get('copy_trade', {}).get('usdc_amount', 0),
                        'success': h.success,
                        'error': h.error_message,
                        'timestamp': h.created_at.strftime('%Y-%m-%d %H:%M') if h.created_at else ''
                    })
                
                return results
                
            except Exception as e:
                logger.error(f"Error getting copy trade history: {e}")
                return []
    
    async def get_copy_trade_stats(self, user_id: int) -> Dict:
        """Get copy trade statistics for a user"""
        async with self.get_session() as session:
            try:
                # Total trades
                total_stmt = select(func.count()).select_from(CopyTradeHistory).where(
                    CopyTradeHistory.user_id == user_id
                )
                total_result = await session.execute(total_stmt)
                total = total_result.scalar()
                
                # Successful trades
                success_stmt = select(func.count()).select_from(CopyTradeHistory).where(
                    CopyTradeHistory.user_id == user_id,
                    CopyTradeHistory.success == True
                )
                success_result = await session.execute(success_stmt)
                successful = success_result.scalar()
                
                return {
                    'total_trades': total,
                    'successful_trades': successful,
                    'failed_trades': total - successful,
                    'success_rate': (successful / total * 100) if total > 0 else 0
                }
                
            except Exception as e:
                logger.error(f"Error getting copy trade stats: {e}")
                return {'total_trades': 0, 'successful_trades': 0, 'failed_trades': 0, 'success_rate': 0}

    # ==================== API CREDENTIALS ====================
    
    async def get_user_api_creds(self, user_id: int, signature_type: int = None) -> Optional[Dict]:
        """Get stored API credentials for a user"""
        async with self.get_session() as session:
            try:
                stmt = select(UserApiCreds).where(UserApiCreds.user_id == user_id)
                result = await session.execute(stmt)
                creds = result.scalars().first()
                
                if not creds:
                    return None
                
                if signature_type is not None and creds.signature_type != signature_type:
                    logger.info(f"Signature type changed for user {user_id}, will re-derive")
                    return None
                
                return {
                    'api_key': self._decrypt_value(creds.api_key),
                    'api_secret': self._decrypt_value(creds.api_secret),
                    'api_passphrase': self._decrypt_value(creds.api_passphrase),
                    'signature_type': creds.signature_type
                }
                
            except Exception as e:
                logger.error(f"Error getting API creds: {e}")
                return None
    
    async def save_user_api_creds(self, user_id: int, creds_dict: Dict) -> bool:
        """Save API credentials for a user"""
        async with self.get_session() as session:
            try:
                stmt = select(UserApiCreds).where(UserApiCreds.user_id == user_id)
                result = await session.execute(stmt)
                existing = result.scalars().first()
                
                if existing:
                    existing.api_key = self._encrypt_value(creds_dict['api_key'])
                    existing.api_secret = self._encrypt_value(creds_dict['api_secret'])
                    existing.api_passphrase = self._encrypt_value(creds_dict['api_passphrase'])
                    existing.signature_type = creds_dict.get('signature_type', 0)
                    existing.updated_at = datetime.now(timezone.utc)
                else:
                    new_creds = UserApiCreds(
                        user_id=user_id,
                        api_key=self._encrypt_value(creds_dict['api_key']),
                        api_secret=self._encrypt_value(creds_dict['api_secret']),
                        api_passphrase=self._encrypt_value(creds_dict['api_passphrase']),
                        signature_type=creds_dict.get('signature_type', 0)
                    )
                    session.add(new_creds)
                
                await session.commit()
                logger.info(f"✅ Saved API credentials for user {user_id}")
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error saving API creds: {e}")
                return False
    
    async def delete_user_api_creds(self, user_id: int) -> bool:
        """Delete API credentials for a user"""
        async with self.get_session() as session:
            try:
                stmt = select(UserApiCreds).where(UserApiCreds.user_id == user_id)
                result = await session.execute(stmt)
                creds = result.scalars().first()
                
                if creds:
                    await session.delete(creds)
                    await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting API creds: {e}")
                return False

    # ==================== MULTI-BUY DETECTION ====================
    
    async def record_buy_for_multibuy(self, trade: Dict, wallet_address: str) -> bool:
        """Record a BUY trade for multi-buy detection"""
        async with self.get_session() as session:
            try:
                if trade.get('side', '').upper() != 'BUY':
                    return False
                
                trade_hash = trade.get('transaction_hash') or trade.get('id') or f"{wallet_address}_{trade.get('timestamp', datetime.now(timezone.utc).isoformat())}"
                
                # Check if already recorded
                stmt = select(MultiBuyRecord).where(MultiBuyRecord.trade_hash == trade_hash)
                result = await session.execute(stmt)
                existing = result.scalars().first()
                
                if existing:
                    return False
                
                market_id = trade.get('condition_id') or trade.get('market_slug') or trade.get('market_id')
                if not market_id:
                    logger.warning(f"No market_id for multi-buy record: {trade}")
                    return False
                
                record = MultiBuyRecord(
                    market_id=str(market_id),
                    market_title=trade.get('title') or trade.get('market', 'Unknown'),
                    outcome=trade.get('outcome', 'YES').upper(),
                    token_id=trade.get('token_id') or trade.get('asset'),
                    wallet_address=wallet_address.lower(),
                    price=float(trade.get('price', 0) or 0),
                    usdc_size=float(trade.get('usdc_size', 0) or 0),
                    trade_hash=trade_hash,
                    timestamp=datetime.now(timezone.utc)
                )
                session.add(record)
                await session.commit()
                logger.debug(f"Recorded buy for multi-buy: {wallet_address[:10]}... on {market_id}")
                return True
                
            except Exception as e:
                await session.rollback()
                if 'UNIQUE constraint' not in str(e):
                    logger.error(f"Error recording buy for multi-buy: {e}")
                return False
    
    async def get_recent_buys_for_market(
        self, 
        market_id: str, 
        outcome: str, 
        hours: int = 1
    ) -> List[Dict]:
        """Get all buys for a market+outcome in the last N hours"""
        async with self.get_session() as session:
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
                
                stmt = (
                    select(MultiBuyRecord)
                    .where(
                        MultiBuyRecord.market_id == str(market_id),
                        MultiBuyRecord.outcome == outcome.upper(),
                        MultiBuyRecord.timestamp >= cutoff
                    )
                    .order_by(MultiBuyRecord.timestamp.asc())
                )
                
                result = await session.execute(stmt)
                records = result.scalars().all()
                
                return [{
                    'wallet_address': r.wallet_address,
                    'price': r.price,
                    'usdc_size': r.usdc_size,
                    'timestamp': r.timestamp,
                    'token_id': r.token_id,
                    'market_title': r.market_title
                } for r in records]
                
            except Exception as e:
                logger.error(f"Error getting recent buys: {e}")
                return []
    
    async def get_multibuy_wallets(
        self, 
        market_id: str, 
        outcome: str, 
        hours: int = 1
    ) -> List[str]:
        """Get unique wallet addresses that bought a market+outcome recently"""
        async with self.get_session() as session:
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
                
                stmt = (
                    select(MultiBuyRecord.wallet_address)
                    .where(
                        MultiBuyRecord.market_id == str(market_id),
                        MultiBuyRecord.outcome == outcome.upper(),
                        MultiBuyRecord.timestamp >= cutoff
                    )
                    .distinct()
                )
                
                result = await session.execute(stmt)
                return [r[0] for r in result.all()]
                
            except Exception as e:
                logger.error(f"Error getting multi-buy wallets: {e}")
                return []
    
    @staticmethod
    def _get_wallet_combo_hash(wallet_addresses: List[str]) -> str:
        """Generate a hash for a combination of wallet addresses"""
        sorted_addresses = sorted([w.lower() for w in wallet_addresses])
        combo_str = ','.join(sorted_addresses)
        return hashlib.md5(combo_str.encode()).hexdigest()[:16]
    
    async def has_multibuy_alert_been_sent(
        self, 
        user_id: int, 
        market_id: str, 
        outcome: str, 
        wallet_addresses: List[str]
    ) -> bool:
        """Check if a multi-buy alert was already sent for this exact combination"""
        async with self.get_session() as session:
            try:
                combo_hash = self._get_wallet_combo_hash(wallet_addresses)
                
                stmt = select(MultiBuyAlertSent).where(
                    MultiBuyAlertSent.user_id == user_id,
                    MultiBuyAlertSent.market_id == str(market_id),
                    MultiBuyAlertSent.outcome == outcome.upper(),
                    MultiBuyAlertSent.wallet_combo_hash == combo_hash
                )
                
                result = await session.execute(stmt)
                existing = result.scalars().first()
                
                return existing is not None
                
            except Exception as e:
                logger.error(f"Error checking multi-buy alert: {e}")
                return False
    
    async def mark_multibuy_alert_sent(
        self, 
        user_id: int, 
        market_id: str, 
        outcome: str, 
        wallet_addresses: List[str]
    ) -> bool:
        """Mark a multi-buy alert as sent"""
        async with self.get_session() as session:
            try:
                combo_hash = self._get_wallet_combo_hash(wallet_addresses)
                
                alert = MultiBuyAlertSent(
                    user_id=user_id,
                    market_id=str(market_id),
                    outcome=outcome.upper(),
                    wallet_combo_hash=combo_hash,
                    wallet_count=len(wallet_addresses)
                )
                session.add(alert)
                await session.commit()
                return True
                
            except Exception as e:
                await session.rollback()
                if 'UNIQUE constraint' not in str(e):
                    logger.error(f"Error marking multi-buy alert sent: {e}")
                return False
    
    async def get_pro_users_tracking_all_wallets(self, wallet_addresses: List[str]) -> List[int]:
        """Get PRO user IDs who are tracking ALL of the specified wallets"""
        async with self.get_session() as session:
            try:
                if not wallet_addresses:
                    return []
                
                wallet_addresses = [w.lower() for w in wallet_addresses]
                wallet_count = len(wallet_addresses)
                
                # Subquery: count how many of these wallets each user tracks
                user_wallet_counts = (
                    select(
                        UserWalletTracking.user_id,
                        func.count(UserWalletTracking.wallet_address).label('tracked_count')
                    )
                    .where(UserWalletTracking.wallet_address.in_(wallet_addresses))
                    .group_by(UserWalletTracking.user_id)
                    .subquery()
                )
                
                # Get users who track ALL wallets AND are PRO
                stmt = (
                    select(UserSubscription.user_id)
                    .join(user_wallet_counts, UserSubscription.user_id == user_wallet_counts.c.user_id)
                    .where(
                        user_wallet_counts.c.tracked_count == wallet_count,
                        UserSubscription.tier == SubscriptionTier.PRO.value
                    )
                )
                
                result = await session.execute(stmt)
                user_ids = [row[0] for row in result.all()]
                
                # Verify PRO subscription hasn't expired
                verified_users = []
                for user_id in user_ids:
                    if await self.is_pro(user_id):
                        verified_users.append(user_id)
                
                return verified_users
                
            except Exception as e:
                logger.error(f"Error getting PRO users for multi-buy: {e}", exc_info=True)
                return []
    
    async def cleanup_old_multibuy_records(self, hours: int = 2):
        """Remove old multi-buy records"""
        async with self.get_session() as session:
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
                stmt = delete(MultiBuyRecord).where(MultiBuyRecord.timestamp < cutoff)
                await session.execute(stmt)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Error cleaning up multi-buy records: {e}")
    
    async def cleanup_old_multibuy_alerts(self, hours: int = 24):
        """Remove old multi-buy alert records"""
        async with self.get_session() as session:
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
                stmt = delete(MultiBuyAlertSent).where(MultiBuyAlertSent.sent_at < cutoff)
                await session.execute(stmt)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Error cleaning up multi-buy alerts: {e}")
    
    async def get_users_with_multibuy_copy_trading(self, wallet_addresses: List[str]) -> List[Dict]:
        """Get users who have multi_buy_only copy trading enabled and track at least one of the specified wallets.
        Per-user threshold enforcement is handled in CopyTradeManager.process_multibuy_copy_trades()."""
        async with self.get_session() as session:
            try:
                if not wallet_addresses:
                    return []
                
                wallet_addresses = [w.lower() for w in wallet_addresses]
                
                # Subquery: find users who track at least one of the buying wallets
                user_wallet_counts = (
                    select(
                        UserWalletTracking.user_id,
                        func.count(UserWalletTracking.wallet_address).label('tracked_count')
                    )
                    .where(UserWalletTracking.wallet_address.in_(wallet_addresses))
                    .group_by(UserWalletTracking.user_id)
                    .subquery()
                )
                
                # Get users with multi_buy_only enabled, tracking at least 1 wallet, with allowances set
                stmt = (
                    select(CopyTradeSettings.user_id)
                    .join(UserWallet, CopyTradeSettings.user_id == UserWallet.user_id)
                    .join(user_wallet_counts, CopyTradeSettings.user_id == user_wallet_counts.c.user_id)
                    .join(UserSubscription, CopyTradeSettings.user_id == UserSubscription.user_id)
                    .where(
                        CopyTradeSettings.enabled == True,
                        CopyTradeSettings.multi_buy_only == True,
                        user_wallet_counts.c.tracked_count >= 1,  # At least 1 tracked wallet
                        UserWallet.allowances_set == True,
                        UserSubscription.tier == SubscriptionTier.PRO.value
                    )
                )
                
                result = await session.execute(stmt)
                user_ids = [row[0] for row in result.all()]
                
                # Verify PRO status
                verified_users = []
                for user_id in user_ids:
                    if await self.is_pro(user_id):
                        verified_users.append({'user_id': user_id})
                
                return verified_users
                
            except Exception as e:
                logger.error(f"Error getting multi-buy copy trading users: {e}", exc_info=True)
                return []

    async def get_sent_trade_hashes(self, transaction_hashes: List[str]) -> set:
        """Batch check which trades have already been sent - much faster than individual checks"""
        if not transaction_hashes:
            return set()
        
        async with self.get_session() as session:
            try:
                stmt = select(SentTrade.transaction_hash).where(
                    SentTrade.transaction_hash.in_(transaction_hashes)
                )
                result = await session.execute(stmt)
                return set(row[0] for row in result.all())
            except Exception as e:
                logger.error(f"Error batch checking sent trades: {e}")
                return set()

    async def has_multibuy_been_processed(
        self,
        market_id: str,
        outcome: str,
        wallet_fingerprint: str
    ) -> bool:
        """Check if a specific multi-buy combination has already been processed."""
        async with self.get_session() as session:
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                
                stmt = select(MultiBuyProcessed).where(
                    MultiBuyProcessed.market_id == market_id,
                    MultiBuyProcessed.outcome == outcome,
                    MultiBuyProcessed.wallet_fingerprint == wallet_fingerprint,
                    MultiBuyProcessed.created_at > cutoff
                ).limit(1)
                
                result = await session.execute(stmt)
                return result.scalars().first() is not None
                
            except Exception as e:
                logger.error(f"Error checking multibuy processed status: {e}")
                return False  # Fail open - allow processing if check fails


    async def mark_multibuy_processed(
        self,
        market_id: str,
        outcome: str,
        wallet_fingerprint: str
    ):
        """Mark a specific multi-buy combination as processed."""
        async with self.get_session() as session:
            try:
                record = MultiBuyProcessed(
                    market_id=market_id,
                    outcome=outcome,
                    wallet_fingerprint=wallet_fingerprint,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(record)
                await session.commit()
                logger.debug(f"Marked multi-buy as processed: {market_id[:20]}... {outcome}")
                
            except IntegrityError:
                # Already exists - this is fine
                await session.rollback()
                logger.debug(f"Multi-buy already marked as processed (duplicate ignored)")
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error marking multibuy as processed: {e}")


    async def cleanup_old_multibuy_processed(self, hours: int = 24):
        """Clean up old multi-buy processed records"""
        async with self.get_session() as session:
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
                stmt = delete(MultiBuyProcessed).where(MultiBuyProcessed.created_at < cutoff)
                result = await session.execute(stmt)
                await session.commit()
                logger.debug(f"Cleaned up {result.rowcount} old multibuy_processed records")
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error cleaning up multibuy_processed: {e}")


# ==================== INITIALIZATION ====================

async def init_db():
    """Initialize database tables"""
    db = Database()
    await db.connect()
    await db.close()
    print("✅ Async database initialized")


def get_db():
    """Get database session (for backward compatibility - use Database().get_session() instead)"""
    raise NotImplementedError(
        "get_db() is deprecated. Use: db = Database(); await db.connect(); async with db.get_session() as session: ..."
    )
