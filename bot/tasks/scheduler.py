# bot/tasks/scheduler.py
"""Optimized scheduler with distributed locking and caching."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from telegram.ext import CallbackContext
from bot.config import Config
from bot.services.cache_strategies import DistributedLock

logger = logging.getLogger(__name__)


class PerformanceMetrics:
    """Track per-cycle performance metrics."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.wallets_checked = 0
        self.wallets_with_trades = 0   # NEW: wallets that actually had new trades
        self.trades_found = 0
        self.notifications_sent = 0
        self.copy_trades_executed = 0  # NEW: copy trades that fired
        self.copy_trades_skipped = 0   # NEW: copy trades skipped (e.g. multi_buy_only)
        self.errors = 0
        self.start_time = None
        self.end_time = None
        self._cleanup_end = None       # NEW: separate cleanup timing

    def mark_cleanup_done(self):
        """Call this right after the 4 DB cleanup calls."""
        self._cleanup_end = datetime.now(timezone.utc)

    def duration(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

    def wallet_check_duration(self) -> float:
        """Time spent only on wallet checks, excluding DB cleanup."""
        if self._cleanup_end and self.end_time:
            return (self.end_time - self._cleanup_end).total_seconds()
        return self.duration()

    def log_summary(self):
        total = self.duration()
        check_t = self.wallet_check_duration()
        cleanup_t = total - check_t

        avg = f"{check_t / self.wallets_checked:.2f}s/wallet" if self.wallets_checked > 0 else "n/a"

        # Single-line structured summary — easy to grep and parse
        logger.info(
            f"✅ Cycle done | "
            f"total={total:.2f}s (cleanup={cleanup_t:.2f}s, checks={check_t:.2f}s) | "
            f"wallets={self.wallets_checked} ({self.wallets_with_trades} active) | "
            f"trades={self.trades_found} | "
            f"notifs={self.notifications_sent} | "
            f"copy=+{self.copy_trades_executed}/-{self.copy_trades_skipped} | "
            f"errors={self.errors} | "
            f"avg={avg}"
        )

class ScheduledTasks:
    """Handles scheduled background jobs with optimizations."""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.polymarket = bot.polymarket
        self.notification_service = bot.notification_service
        self.copy_manager = getattr(bot, 'copy_manager', None)
        self.cache = bot.cache
        self.metrics = PerformanceMetrics()
    
    async def check_wallets_for_trades(self, context: CallbackContext):
        """Check wallets with distributed locking."""
        
        # Distributed lock for horizontal scaling
        async with DistributedLock(self.cache, "wallet_monitor", ttl=120) as acquired:
            if not acquired:
                logger.info("⏭️ Another instance is running wallet monitor, skipping")
                return
            
            await self._check_wallets_internal(context)
    
    async def _check_wallets_internal(self, context: CallbackContext):
        try:
            self.metrics.reset()
            self.metrics.start_time = datetime.now(timezone.utc)
            logger.info("🔍 Starting wallet monitor cycle")

            await self.db.cleanup_old_sent_trades(days=7)
            await self.db.cleanup_old_multibuy_records(hours=2)
            await self.db.cleanup_old_multibuy_alerts(hours=24)
            await self.db.cleanup_old_multibuy_processed(hours=24)
            self.metrics.mark_cleanup_done()

            all_wallets = await self.db.get_all_tracked_wallets()
            if not all_wallets:
                logger.info("No wallets to monitor")
                return

            # ✅ FIX: deduplicate by address — multiple users tracking the same
            # wallet must NOT result in multiple concurrent _check_single_wallet calls.
            # Per-user fan-out happens inside send_trade_notification, not here.
            seen_addresses = set()
            unique_wallets = []
            for w in all_wallets:
                addr = w['address']
                if addr not in seen_addresses:
                    seen_addresses.add(addr)
                    unique_wallets.append(w)

            skipped = len(all_wallets) - len(unique_wallets)
            if skipped > 0:
                logger.debug(f"Deduplicated {skipped} duplicate wallet entries from {len(all_wallets)} → {len(unique_wallets)}")

            logger.info(f"🎯 Checking {len(unique_wallets)} wallets")

            semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT_WALLET_CHECKS)

            async def check_with_limit(wallet_data):
                async with semaphore:
                    result = await self._check_single_wallet(wallet_data, context)
                    await asyncio.sleep(0)  # Yield to event loop so webhook handlers aren't starved
                    return result

            results = await asyncio.gather(
                *[check_with_limit(w) for w in unique_wallets],   # ✅ use unique_wallets
                return_exceptions=True
            )

            for wallet, result in zip(unique_wallets, results):
                if isinstance(result, Exception):
                    logger.error(f"Error checking {wallet['address'][:10]}: {result}")
                    self.metrics.errors += 1
                elif result:
                    self.metrics.trades_found += result.get('trades_found', 0)
                    self.metrics.notifications_sent += result.get('notifications_sent', 0)
                    self.metrics.copy_trades_executed += result.get('copy_trades_executed', 0)  # NEW
                    self.metrics.copy_trades_skipped += result.get('copy_trades_skipped', 0)    # NEW
                    if result.get('trades_found', 0) > 0:
                        self.metrics.wallets_with_trades += 1                                    # NEW

            self.metrics.wallets_checked = len(all_wallets)
            self.metrics.end_time = datetime.now(timezone.utc)
            self.metrics.log_summary()   # ← always log, no flag needed

        except Exception as e:
            logger.error(f"Error in scheduled check: {e}", exc_info=True)

    
    async def _check_single_wallet(self, wallet_data: dict, context: CallbackContext) -> dict:
        """Check a single wallet for new trades"""
        wallet_address = wallet_data['address']
        result = {'trades_found': 0, 'notifications_sent': 0}
        
        try:
            last_check = await self.db.get_last_check_time(wallet_address)
            is_first_run = last_check is None
            
            if is_first_run:
                last_check = datetime.now(timezone.utc) - timedelta(hours=1)
            
            # Fetch trades
            trades = await self.polymarket.get_recent_trades(
                wallet_address,
                since=last_check
            )
            
            if not trades:
                await self.db.update_last_check_time(wallet_address)
                return result
            
            # Batch check for already-sent trades
            all_tx_hashes = [t.get('transaction_hash') for t in trades if t.get('transaction_hash')]
            already_sent_hashes = await self.db.get_sent_trade_hashes(all_tx_hashes)
            
            new_trades = [
                t for t in trades 
                if t.get('transaction_hash') and t.get('transaction_hash') not in already_sent_hashes
            ]
            
            if is_first_run:
                # Mark existing trades as seen
                for trade in new_trades:
                    if trade.get('transaction_hash'):
                        await self.db.mark_trade_as_sent(trade['transaction_hash'], wallet_address)
            else:
                # Process new trades
                result['trades_found'] = len(new_trades)
                
                for trade in new_trades:
                    # Send notification + execute copy trades
                    await self.notification_service.send_trade_notification(
                        wallet_data, trade, context
                    )
                    
                    # Mark as sent
                    if trade.get('transaction_hash'):
                        await self.db.mark_trade_as_sent(trade['transaction_hash'], wallet_address)
                    
                    result['notifications_sent'] += 1
                    
                    await asyncio.sleep(0)  # Yield to event loop between trades
            
            await self.db.update_last_check_time(wallet_address)
            
            return result
            
        except Exception as e:
            logger.error(f"Error checking wallet {wallet_address[:10]}: {e}")
            raise
    
    async def refresh_leaderboard(self, context: CallbackContext):
        """Refresh leaderboard with distributed locking."""
        
        # Distributed lock for horizontal scaling
        async with DistributedLock(self.cache, "leaderboard_refresh", ttl=300) as acquired:
            if not acquired:
                logger.info("⏭️ Another instance is refreshing leaderboard, skipping")
                return
            
            await self._refresh_leaderboard_internal(context)
    
    async def _refresh_leaderboard_internal(self, context: CallbackContext):
        """Internal leaderboard refresh"""
        try:
            logger.info("🔄 Refreshing leaderboard...")
            
            traders = await self.polymarket.get_leaderboard(
                limit=getattr(Config, 'LEADERBOARD_TOP_N', 10),
                time_period=getattr(Config, 'LEADERBOARD_TIME_PERIOD', 'ALL'),
                category=getattr(Config, 'LEADERBOARD_CATEGORY', 'OVERALL'),
                order_by='PNL'
            )
            
            if traders:
                count = await self.db.update_leaderboard_wallets(traders)
                logger.info(f"✅ Updated {count} top traders from leaderboard")
            else:
                logger.warning("⚠️ Failed to fetch leaderboard data")
                
        except Exception as e:
            logger.error(f"Error refreshing leaderboard: {e}", exc_info=True)
