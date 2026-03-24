# bot/core.py
"""Refactored core bot class for scalability."""

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from bot.handlers.displays import DisplayViews
from bot.config import Config
from bot.services.database import Database
from bot.services.cache import CacheManager
from bot.services.notification_queue import NotificationQueue  # NEW
from bot.polymarket_client import PolymarketClient
from bot.trading import WalletManager, CopyTradeManager
from bot.trading.commands import TradingCommands
from bot.handlers import CommandHandlers, CallbackHandlers, MenuHandlers
from bot.services.notifications import NotificationService
from bot.tasks.scheduler import ScheduledTasks
from bot.services.cache_strategies import CacheStrategy, WalletStatsCache, MarketDataCache
from bot.services.webhooks import WebhookService

logger = logging.getLogger(__name__)


class TagwiseBot:
    """Telegram bot for Polymarket tracking."""
    
    def __init__(self):
        self.config = Config
        
        # Initialize async components
        self.db = Database(Config.DATABASE_URL)
        self.cache = CacheManager(Config.REDIS_URL)
        self.polymarket = PolymarketClient(self.cache)
        
        # State tracking
        self.awaiting_wallet_input = set()
        
        # These will be initialized after db connects
        self.wallet_manager = None
        self.copy_manager = None
        self.notification_queue = None  # NEW
        self.notification_service = None
        self.scheduled_tasks = None
        self.menu_handlers = None
        self.trading_commands = None
        
        self.app: Application = None
        
        # Background task handles
        self._monitor_task = None
        self._leaderboard_task = None
        self.displays = None
        self.cache_strategy = None
        self.wallet_stats_cache = None
        self.market_data_cache = None
        self.webhook_service = None   # ← add this
        self._monitor_task = None
        self._leaderboard_task = None
    
    async def initialize(self):
        """Initialize all async components."""
        logger.info("Initializing bot components...")
        
        await self.db.connect()
        await self.cache.connect()
        
        # ✅ Initialize caching strategies
        self.cache_strategy = CacheStrategy(self.cache)
        self.wallet_stats_cache = WalletStatsCache(self.cache_strategy)
        self.market_data_cache = MarketDataCache(self.cache_strategy)
        
        # ✅ Pass cache to polymarket client
        self.polymarket.set_cache(self.cache)
        await self.polymarket.connect()
        
        # ✅ Attach cache to database for user query caching
        self.db.cache = self.cache
        
        # ✅ Wrap hot DB methods with Redis caching (30s TTL)
        self._wrap_db_with_cache()
        
        # Initialize managers that depend on db
        self.wallet_manager = WalletManager(self.db)
        self.copy_manager = CopyTradeManager(self.db, self.wallet_manager)
        
        # Build Telegram application (needed for notification queue)
        self.app = (
            Application.builder()
            .token(Config.TELEGRAM_BOT_TOKEN)
            .concurrent_updates(True)
            .build()
        )
        
        # ✅ Initialize notification queue with bot instance
        rate_limit = getattr(Config, 'TELEGRAM_RATE_LIMIT_PER_SECOND', 20)
        self.notification_queue = NotificationQueue(
            bot=self.app.bot,
            rate_limit=rate_limit
        )
        await self.notification_queue.start()
        logger.info(f"✅ Notification queue started (rate limit: {rate_limit}/s)")
        
        # ✅ Initialize notification service BEFORE scheduled tasks
        self.notification_service = NotificationService(
            self.db, 
            self.copy_manager,
            self.notification_queue
        )
        
        # ✅ NOW initialize scheduled tasks (notification_service is ready)
        self.scheduled_tasks = ScheduledTasks(self)
        
        # Initialize other handlers
        self.menu_handlers = MenuHandlers(self)
        self.trading_commands = TradingCommands(self.db, self.wallet_manager, self.copy_manager, bot=self) 
        self.displays = DisplayViews(self)
        
        self._register_handlers()
        
        logger.info("✅ Bot initialized successfully")
    
    async def shutdown(self):
        """Clean shutdown of all components."""
        logger.info("Shutting down...")
        
        # Cancel background tasks
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        if self._leaderboard_task:
            self._leaderboard_task.cancel()
            try:
                await self._leaderboard_task
            except asyncio.CancelledError:
                pass
        
        # ✅ NEW: Stop notification queue
        if self.notification_queue:
            logger.info("Stopping notification queue...")
            await self.notification_queue.stop()
        
        await self.polymarket.close()
        await self.cache.close()
        await self.db.close()
        
        logger.info("✅ Shutdown complete")
    
    def _wrap_db_with_cache(self):
        """Wrap frequently-called DB methods with Redis caching to reduce latency."""
        original_is_pro = self.db.is_pro
        original_get_tracked_wallets = self.db.get_tracked_wallets
        cache = self.cache

        async def cached_is_pro(user_id: int) -> bool:
            try:
                cached = await cache.get_cached_is_pro(user_id)
                if cached is not None:
                    return cached
            except Exception:
                pass  # Redis down, fall through to DB
            result = await original_is_pro(user_id)
            try:
                await cache.set_cached_is_pro(user_id, result, ttl=300)
            except Exception:
                pass
            return result

        async def cached_get_tracked_wallets(user_id: int, wallet_type: str = None) -> list:
            # Only cache the no-filter case (most common path)
            if wallet_type is None:
                try:
                    cached = await cache.get_cached_tracked_wallets(user_id)
                    if cached is not None:
                        return cached
                except Exception:
                    pass
            result = await original_get_tracked_wallets(user_id, wallet_type)
            if wallet_type is None:
                try:
                    await cache.set_cached_tracked_wallets(user_id, result, ttl=60)
                except Exception:
                    pass
            return result

        self.db.is_pro = cached_is_pro
        self.db.get_tracked_wallets = cached_get_tracked_wallets
        logger.info("✅ DB query caching enabled (is_pro: 300s, get_tracked_wallets: 60s TTL)")

    def _register_handlers(self):
        """Register all command and callback handlers."""
        cmd = CommandHandlers(self)
        cb = CallbackHandlers(self)
        
        # ============ COMMANDS ============
        self.app.add_handler(CallbackQueryHandler(cb.welcome_callback, pattern="^welcome_"))
        self.app.add_handler(CommandHandler("start", cmd.start))
        self.app.add_handler(CommandHandler("help", cmd.help_command))
        self.app.add_handler(CommandHandler("account", cmd.account_command))
        self.app.add_handler(CommandHandler("upgrade", cmd.upgrade_command))
        self.app.add_handler(CommandHandler("activate", cmd.admin_activate_pro))
        self.app.add_handler(CommandHandler("toptraders", cmd.top_traders))
        self.app.add_handler(CommandHandler("track", cmd.track_wallet))
        self.app.add_handler(CommandHandler("name", cmd.name_wallet))
        self.app.add_handler(CommandHandler("wallets", cmd.list_wallets))
        self.app.add_handler(CommandHandler("untrack", cmd.untrack_wallet))
        self.app.add_handler(CommandHandler("stats", cmd.wallet_stats))
        self.app.add_handler(CommandHandler("start", cmd.start)) 
        self.app.add_handler(CommandHandler("performance", cmd.performance_command))
        self.app.add_handler(CommandHandler("claim", cmd.claim_command))

        # ============ TRADING HANDLERS (from TradingCommands) ============
        for handler in self.trading_commands.get_handlers():
            self.app.add_handler(handler)
        
        # ============ MENU CALLBACKS ============
        self.app.add_handler(CallbackQueryHandler(cb.menu_callback, pattern="^menu_"))
        
        # ============ TOP TRADERS CALLBACKS ============
        self.app.add_handler(CallbackQueryHandler(cb.toptraders_category_callback, pattern="^topcat_"))
        self.app.add_handler(CallbackQueryHandler(cb.toptraders_callback, pattern="^top_"))
        self.app.add_handler(CallbackQueryHandler(cb.track_leaderboard_callback, pattern="^trackld_"))
        
        # ============ WALLET TRACKING CALLBACKS ============
        self.app.add_handler(CallbackQueryHandler(cb.track_wallet_callback, pattern="^trackwallet_"))
        self.app.add_handler(CallbackQueryHandler(cb.untrack_callback, pattern="^untrack_"))

        # ✅ NEW: Referral callbacks
        self.app.add_handler(CallbackQueryHandler(cb.referral_share_callback, pattern="^referral_"))
        
        # ============ UTILITY CALLBACKS ============
        self.app.add_handler(CallbackQueryHandler(cb.noop_callback, pattern="^noop$"))
        
        # ============ TEXT MESSAGE HANDLER (must be last) ============
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            cb.handle_text_message
        ))
    

    async def _run_wallet_monitor(self):
        """Background task that monitors wallets for new trades."""
        interval = getattr(Config, 'WALLET_CHECK_INTERVAL', 30)
        
        logger.info(f"🚀 Starting wallet monitor (interval: {interval}s)")
        
        class FakeContext:
            def __init__(self, bot):
                self.bot = bot
        
        context = FakeContext(self.app.bot)
        
        while True:
            try:
                await self.scheduled_tasks.check_wallets_for_trades(context)
            except Exception as e:
                logger.error(f"Error in wallet monitor: {e}", exc_info=True)
            
            await asyncio.sleep(interval)
    
    async def _run_leaderboard_refresh(self):
        """Background task that refreshes the leaderboard."""
        # Refresh leaderboard every 6 hours
        interval = getattr(Config, 'LEADERBOARD_REFRESH_INTERVAL', 6 * 60 * 60)
        
        logger.info(f"🚀 Starting leaderboard refresh (interval: {interval}s)")
        
        class FakeContext:
            def __init__(self, bot):
                self.bot = bot
        
        context = FakeContext(self.app.bot)
        
        # Initial refresh on startup
        await asyncio.sleep(10)  # Wait for bot to fully start
        
        while True:
            try:
                await self.scheduled_tasks.refresh_leaderboard(context)
            except Exception as e:
                logger.error(f"Error refreshing leaderboard: {e}", exc_info=True)
            
            await asyncio.sleep(interval)
    
    async def run_polling(self, shutdown_event: asyncio.Event = None):
        """Run bot with polling (for development)."""
        await self.initialize()

        try:
            await self.app.initialize()
            await self.app.start()
            await self.app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            
            logger.info("🤖 Bot is running with polling...")
            
            # Start background tasks
            self._monitor_task = asyncio.create_task(self._run_wallet_monitor())
            self._leaderboard_task = asyncio.create_task(self._run_leaderboard_refresh())
            
            # Wait for shutdown signal or run forever
            if shutdown_event:
                await shutdown_event.wait()
            else:
                while True:
                    await asyncio.sleep(1)
                
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Stopping bot...")
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            await self.shutdown()
    
    async def run_webhook(self, webhook_url: str, port: int = 8443, shutdown_event: asyncio.Event = None):
        """Run bot with webhook (for production)."""
        await self.initialize()
        
        try:
            await self.app.initialize()
            await self.app.start()
            
            await self.app.updater.start_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=Config.TELEGRAM_BOT_TOKEN,
                webhook_url=f"{webhook_url}/{Config.TELEGRAM_BOT_TOKEN}"
            )
            
            logger.info(f"🤖 Bot is running with webhook on port {port}...")

            self.webhook_service = WebhookService(self)
            await self.webhook_service.start_webhook_server()
        
            # Start background tasks
            self._monitor_task = asyncio.create_task(self._run_wallet_monitor())
            self._leaderboard_task = asyncio.create_task(self._run_leaderboard_refresh())
            
            # Wait for shutdown signal or run forever
            if shutdown_event:
                await shutdown_event.wait()
            else:
                while True:
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Stopping bot...")
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            await self.shutdown()


    async def _auto_provision_wallet(self, user_id: int):
        try:
            existing = await self.wallet_manager.get_wallet(user_id)

            if existing:
                # ADD THIS BLOCK — re-provision legacy wallets with no Privy ID
                if not existing.get('privy_wallet_id'):
                    logger.info(f"[auto_provision] Migrating legacy wallet for user {user_id}")
                    # Delete old record and re-create via Privy
                    await self.wallet_manager.delete_wallet(user_id)
                    existing = None  # Fall through to creation below

                else:
                    # Existing Privy wallet — check setup completeness
                    safe_address = existing.get('safe_address')
                    if safe_address:
                        status = await asyncio.to_thread(
                            self.wallet_manager.builder.get_safe_status,
                            existing['address']
                        )
                        if not status.get('allowances_set'):
                            logger.info(f"[auto_provision] Completing setup for user {user_id}")
                            await self.wallet_manager.setup_safe(user_id)
                    return

            # No wallet (or just deleted legacy one) — create fresh via Privy
            logger.info(f"[auto_provision] Creating wallet for user {user_id}")
            result = await self.wallet_manager.create_wallet(user_id)
            if not result['success']:
                logger.warning(f"[auto_provision] Wallet creation failed for {user_id}: {result.get('error')}")
                return

            setup = await self.wallet_manager.setup_safe(user_id)
            if not setup['success']:
                logger.warning(f"[auto_provision] Setup failed for {user_id}: {setup.get('error')}")
            else:
                logger.info(f"[auto_provision] ✅ Wallet fully provisioned for user {user_id}")

        except Exception as e:
            logger.error(f"[auto_provision] Error for user {user_id}: {e}", exc_info=True)
