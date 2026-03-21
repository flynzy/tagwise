import os
from dotenv import load_dotenv

load_dotenv()


class TierLimits:
    """Tier-based feature limits"""
    # FREE tier
    FREE_MAX_CUSTOM_WALLETS = 3
    FREE_MAX_TAGWISE_TRADERS = 5
    FREE_CONFIDENCE_SCORES = False
    FREE_LEADERBOARD_FILTERS = False
    
    # PRO tier
    PRO_MAX_CUSTOM_WALLETS = 100
    PRO_MAX_TAGWISE_TRADERS = 50
    PRO_CONFIDENCE_SCORES = True
    PRO_LEADERBOARD_FILTERS = True
    
    # Pricing
    PRO_PRICE_MONTHLY = 10.00
    PRO_PRICE_ANNUAL = 199.00


class NOWPaymentsConfig:
    """NOWPayments gateway configuration"""
    API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "")
    IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET", "")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

    @classmethod
    def get_callback_url(cls) -> str:
        return f"{cls.WEBHOOK_URL}/webhook/nowpayments"



class Config:
    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    
    # Admin user IDs (comma-separated)
    ADMIN_USER_IDS = [
        int(x.strip()) 
        for x in os.getenv("ADMIN_USER_IDS", "").split(",") 
        if x.strip()
    ]
    
    # Polymarket
    POLYMARKET_API_URL = "https://clob.polymarket.com"
    POLYMARKET_DATA_API = "https://data-api.polymarket.com"
    PRIVATE_KEY = os.getenv("PRIVATE_KEY")
    PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS")

    # Database - default to SQLite for easier setup
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///tagwise.db")
    
    # Redis (optional)
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    # Monitoring
    POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))
    MIN_TRADE_VALUE = float(os.getenv("MIN_TRADE_VALUE", "100"))
    MIN_WALLET_ROI = float(os.getenv("MIN_WALLET_ROI", "10"))
    LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))
    
    # Features
    PRO_ENABLED = os.getenv("PRO_ENABLED", "true").lower() == "true"
    CONFIDENCE_SCORE_ENABLED = os.getenv("CONFIDENCE_SCORE_ENABLED", "true").lower() == "true"
    
    # URLs
    WEBSITE_URL = os.getenv("WEBSITE_URL", "https://tagwise.xyz")
    X_URL = os.getenv("X_URL", "https://twitter.com/tagwise")
    
    # Leaderboard
    LEADERBOARD_TOP_N = 10
    LEADERBOARD_TIME_PERIOD = "ALL"
    LEADERBOARD_CATEGORY = "OVERALL"
    LEADERBOARD_ORDER_BY = "PNL"
    LEADERBOARD_CACHE_TTL = 6 * 60 * 60

    # Polymarket Builder Program credentials
    POLYMARKET_BUILDER_API_KEY = os.getenv("POLYMARKET_BUILDER_API_KEY")
    POLYMARKET_BUILDER_SECRET = os.getenv("POLYMARKET_BUILDER_SECRET")
    POLYMARKET_BUILDER_PASSPHRASE = os.getenv("POLYMARKET_BUILDER_PASSPHRASE")
    
    # Polygon RPC
    POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")

    # Worker settings
    TRADE_CHECK_INTERVAL: int = int(os.getenv("TRADE_CHECK_INTERVAL", "60"))
    LEADERBOARD_REFRESH_INTERVAL: int = int(os.getenv("LEADERBOARD_REFRESH_INTERVAL", "21600"))

    # Webhook
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", 8443))
    ENV = os.getenv("ENV", "development")

    WALLET_ENCRYPTION_KEY = os.getenv("WALLET_ENCRYPTION_KEY")

    # Privy wallet infrastructure
    PRIVY_APP_ID = os.getenv("PRIVY_APP_ID")
    PRIVY_APP_SECRET = os.getenv("PRIVY_APP_SECRET")
    PRIVY_AUTH_KEY = os.getenv("PRIVY_AUTH_KEY")
    PRIVY_QUORUM_ID = os.getenv("PRIVY_QUORUM_ID")

    
    # ==================== NEW: PERFORMANCE SETTINGS ====================
    
    # Concurrency limits
    MAX_CONCURRENT_WALLET_CHECKS = int(os.getenv("MAX_CONCURRENT_WALLET_CHECKS", "10"))
    MAX_CONCURRENT_NOTIFICATIONS = int(os.getenv("MAX_CONCURRENT_NOTIFICATIONS", "20"))
    
    # Rate limiting
    TELEGRAM_RATE_LIMIT_PER_SECOND = int(os.getenv("TELEGRAM_RATE_LIMIT_PER_SECOND", "25"))
    POLYMARKET_API_RATE_LIMIT = int(os.getenv("POLYMARKET_API_RATE_LIMIT", "5"))
    
    # Performance monitoring
    ENABLE_PERFORMANCE_METRICS = os.getenv("ENABLE_PERFORMANCE_METRICS", "true").lower() == "true"
    SLOW_OPERATION_THRESHOLD_SECONDS = float(os.getenv("SLOW_OPERATION_THRESHOLD_SECONDS", "5.0"))

    BOT_USERNAME = "tagwise_bot"
