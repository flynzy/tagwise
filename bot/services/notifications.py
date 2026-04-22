# bot/services/notifications.py

"""Trade notification service."""

import logging
from collections import Counter
from datetime import datetime, timezone
import asyncio
from typing import Optional

from telegram.ext import CallbackContext
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from bot.services.analytics import ConfidenceScorer, get_confidence_emoji

logger = logging.getLogger(__name__)


def format_multibuy_notification(
    market_title: str,
    outcome: str,
    wallet_buys: list[dict],
    wallet_names: dict = None,
    show_confidence_boost: bool = False  # Deprecated
) -> str:
    """Format a multi-buy alert notification with aggregated wallet data."""
    wallet_names = wallet_names or {}

    wallet_aggregates = {}
    for buy in wallet_buys:
        wallet_addr = buy.get('wallet_address', '')
        if not wallet_addr:
            continue
        price = float(buy.get('price', 0))
        usdc_size = float(buy.get('usdc_size', 0))
        if usdc_size <= 0:
            continue
        if wallet_addr not in wallet_aggregates:
            wallet_aggregates[wallet_addr] = {
                'address': wallet_addr,
                'total_value': 0,
                'total_shares': 0,
                'trades_count': 0,
                'price_data': []
            }
        agg = wallet_aggregates[wallet_addr]
        agg['total_value'] += usdc_size
        agg['total_shares'] += (usdc_size / price) if price > 0 else 0
        agg['trades_count'] += 1
        agg['price_data'].append({'price': price, 'size': usdc_size})

    for addr, data in wallet_aggregates.items():
        total_value = data['total_value']
        if total_value > 0:
            weighted_sum = sum(p['price'] * p['size'] for p in data['price_data'])
            data['avg_price'] = weighted_sum / total_value
        else:
            data['avg_price'] = 0

    valid_wallets = [data for data in wallet_aggregates.values() if data['total_value'] > 0]
    if not valid_wallets:
        logger.warning("No valid wallets with value > 0 for multi-buy notification")
        return None

    valid_wallets.sort(key=lambda x: x['total_value'], reverse=True)

    wallet_count = len(valid_wallets)
    total_value = sum(w['total_value'] for w in valid_wallets)
    total_shares = sum(w['total_shares'] for w in valid_wallets)
    avg_price = total_value / total_shares if total_shares > 0 else 0

    if wallet_count >= 4:
        signal_emoji = "🟢🟢🟢"
        signal_text = "VERY STRONG"
    elif wallet_count >= 3:
        signal_emoji = "🟢🟢"
        signal_text = "STRONG"
    else:
        signal_emoji = "🟢"
        signal_text = "CONFIRMED"

    message = f"""
{signal_emoji} **MULTI-BUY ALERT!** {signal_emoji}

**{wallet_count} tracked wallets** are buying the same position!

**Market:** {market_title}
**Position:** {outcome}
**Signal Strength:** {signal_text}

**Buyers:"""

    for i, wallet_data in enumerate(valid_wallets[:10], 1):
        addr = wallet_data['address']
        name = wallet_names.get(addr) or f"{addr[:6]}...{addr[-4:]}"
        avg_price_wallet = wallet_data['avg_price']
        total_value_wallet = wallet_data['total_value']
        trades_count = wallet_data['trades_count']
        trades_suffix = f" ({trades_count} trades)" if trades_count > 1 else ""
        message += f"\n  {i}. **{name}** - ${total_value_wallet:,.0f} @ ${avg_price_wallet:.3f}{trades_suffix}"

    if len(valid_wallets) > 10:
        remaining_count = len(valid_wallets) - 10
        remaining_value = sum(w['total_value'] for w in valid_wallets[10:])
        message += f"\n  _...and {remaining_count} more (${remaining_value:,.0f})_"

    message += f"""

**Combined Value:** ${total_value:,.0f}
**Avg Entry Price:** ${avg_price:.3f}
⏰ All trades within the last hour
"""
    return message.strip()


def format_multibuy_copy_notification(
    market_title: str,
    outcome: str,
    wallet_count: int,
    copy_amount: float,
    order_id: str = None,
    success: bool = True,
    error: str = None,
    wallet_lines: list[str] = None,  # ✅ NEW: pre-formatted wallet strings
    price: float = None,
) -> str:
    """Format notification for multi-buy copy trade execution"""
    wallets_section = ""
    if wallet_lines:
        wallets_section = "\n**Triggered by:**\n" + "\n".join(f"• {w}" for w in wallet_lines) + "\n"

    if success:
        price_line = f"\n• Price: {format_price_cents(price)}" if price is not None else ""
        return (
            f"🤖 **Multi-Buy Copy Trade Executed!**\n\n"
            f"**Market:** {market_title[:50]}\n"
            f"**Position:** {outcome}\n"
            f"**Your Trade:**\n"
            f"• Amount: ${copy_amount:.2f}"
            f"{price_line}\n"
            f"• Order ID: `{order_id or 'N/A'}`\n"
            f"✅ Trade executed successfully!"
        )
    else:
        return f"""
⚠️ **Multi-Buy Copy Trade Failed**

**Market:** {market_title[:50]}
**Position:** {outcome}
**Trigger:** {wallet_count} wallets bought
{wallets_section}
**Reason:** {error or 'Unknown error'}

_Check your wallet balance and settings_
""".strip()



def format_wallet_address(address: str) -> str:
    """Format wallet address for display"""
    return address


def format_price_cents(price: float) -> str:
    """Format price as cents (e.g. 0.40 -> 40.0¢)"""
    cents = price * 100
    return f"{cents:.1f}¢"


def format_confidence_bar(score: int) -> str:
    """Format confidence score as a visual bar"""
    filled = "█" * score
    empty = "░" * (10 - score)
    return f"{filled}{empty}"


def parse_error_message(error: str) -> str:
    """Convert API/system errors into user-friendly messages"""
    error_lower = error.lower()

    if 'insufficient liquidity' in error_lower or 'no match' in error_lower or 'liquidity' in error_lower:
        return "Insufficient liquidity"
    if 'not enough balance' in error_lower or 'allowance' in error_lower or 'insufficient' in error_lower:
        return "Insufficient balance"
    if 'no position' in error_lower or 'position too small' in error_lower:
        return "No position to sell"
    # ✅ NEW: FOK = Fill or Kill — order killed because not enough liquidity at that moment
    if 'fok' in error_lower or "fully filled or killed" in error_lower or "couldn't be fully filled" in error_lower:
        return "Low liquidity — market moved too fast. Try reducing your buy amount in settings."
    if 'order failed' in error_lower or 'failed to execute' in error_lower:
        return "Order execution failed"
    if 'token id' in error_lower or 'could not find token' in error_lower:
        return "Market data unavailable"
    if 'timeout' in error_lower or 'connection' in error_lower:
        return "Connection error — try again"
    if 'price' in error_lower and 'invalid' in error_lower:
        return "Invalid price"
    return "Trade execution failed"



def format_confidence_section(confidence_score_obj) -> str:
    """Format the confidence score section for PRO alerts"""
    score = confidence_score_obj.score
    percentage = confidence_score_obj.percentage
    factors = confidence_score_obj.factors

    stars = "⭐" * score
    emoji = get_confidence_emoji(score)

    if score >= 4:
        label = "High Confidence"
    elif score >= 3:
        label = "Medium Confidence"
    else:
        label = "Low Confidence"

    section = f"\n\n{emoji} **Confidence:** {stars} ({percentage:.0f}%)"
    section += f"\n_{label}_"

    if score >= 4:
        perf = factors.get('wallet_performance', {})
        rep = factors.get('wallet_reputation', {})
        if perf.get('win_rate', 0) > 0.6:
            section += f"\n• Win Rate: {perf.get('win_rate', 0)*100:.0f}%"
        rank = rep.get('leaderboard_rank')
        if rank and rank <= 25:
            section += f"\n• Top #{rank} Trader"
        if factors.get('multi_buy_bonus', 0) > 0:
            section += "\n• 🔥 Multi-Buy Signal"

    return section


def format_trade_notification(
    trade: dict,
    wallet_address: str,
    include_confidence: bool = False,
    confidence_score_obj=None
) -> str:
    """Format trade data into a notification message."""
    wallet_short = format_wallet_address(wallet_address)
    wallet_name = trade.get('wallet_name') or wallet_short
    market = trade.get('title') or trade.get('market', 'Unknown Market')
    market_slug = trade.get('market_slug') or trade.get('slug')
    outcome = trade.get('outcome', 'YES')
    side = trade.get('side', 'BUY')
    size = float(trade.get('size', 0) or 0)
    price = float(trade.get('price', 0) or 0)
    usdc_size = float(trade.get('usdc_size', 0) or 0) or (size * price)
    emoji = "🟢" if side == "BUY" else "🔴"

    if market_slug:
        market_line = f"**Market:** {market} [(view)](https://polymarket.com/event/{market_slug})"
    else:
        market_line = f"**Market:** {market}"

    wallet_short_display = f"{wallet_address[:6]}...{wallet_address[-4:]}" if len(wallet_address) > 10 else wallet_address
    wallet_line = f"**Wallet:** {wallet_name} - `{wallet_short_display}`" if wallet_name != wallet_address else f"**Wallet:** `{wallet_short_display}`"

    message = (
        f"{emoji} **New Trade Alert!**\n\n"
        f"{wallet_line}\n\n"
        f"{market_line}\n\n"
        f"**Trade:**\n"
        f"• Action: {side} {outcome}\n"
        f"• Size: {size:.2f} shares\n"
        f"• Price: {format_price_cents(price)}\n"
        f"• Value: ${usdc_size:.2f}"
    )
    if include_confidence and confidence_score_obj is not None:
        message += format_confidence_section(confidence_score_obj)

    return message.strip()


class NotificationService:
    """Handles sending trade notifications to users."""

    def __init__(self, db, copy_manager, notification_queue=None):
        self.db = db
        self.copy_manager = copy_manager
        self.notification_queue = notification_queue
        self._multibuy_processed_cache = {}

    async def send_trade_notification(self, wallet_data: dict, trade: dict, context: CallbackContext):
        """Send trade notification to all users tracking this wallet AND execute copy trades."""
        try:
            wallet_address = wallet_data['address']

            await self.check_and_process_multibuy(trade, wallet_address, context)

            user_ids = await self.db.get_users_tracking_wallet(wallet_address)
            if not user_ids:
                return

            for user_id in user_ids:
                try:
                    copy_settings = await self.copy_manager.get_user_settings(user_id)
                    if copy_settings.multi_buy_only:
                        logger.debug(f"Skipping notification for user {user_id} (multi-buy only mode)")
                        continue

                    display_name = await self.db.get_wallet_display_name(user_id, wallet_address)
                    trade['wallet_name'] = display_name
                    is_pro = await self.db.is_pro(user_id)

                    confidence_score_obj = None
                    if is_pro:
                        wallet_stats = await self.db.get_wallet_stats_for_confidence(wallet_address)
                        scorer = ConfidenceScorer()
                        confidence_score_obj = scorer.calculate(
                            wallet_stats=wallet_stats,
                            trade=trade,
                            is_multi_buy=False
                        )

                    message = format_trade_notification(
                        trade, wallet_address,
                        include_confidence=is_pro,
                        confidence_score_obj=confidence_score_obj
                    )
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("📊 View Dashboard", callback_data="menu_main")]
                    ])

                    if self.notification_queue:
                        await self.notification_queue.enqueue(
                            user_id=user_id, message=message, reply_markup=keyboard, priority=5
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=user_id, text=message, parse_mode='Markdown', reply_markup=keyboard
                        )
                except Exception as e:
                    logger.error(f"Failed to send notification to user {user_id}: {e}")

            await self._execute_copy_trades(trade, wallet_address, context)

        except Exception as e:
            logger.error(f"Error in send_trade_notification: {e}", exc_info=True)


    async def _execute_copy_trades(self, trade: dict, wallet_address: str, context: CallbackContext):
        """Execute copy trades for users tracking this wallet."""
        try:
            logger.debug(f"🤖 Processing copy trades for wallet {wallet_address[:10]}...")
            copy_results = await self.copy_manager.process_trade_for_copiers(
                trade=trade, source_wallet=wallet_address, context=context
            )

            for result in copy_results:
                user_id = result.get('user_id')
                if not user_id:
                    continue
                try:
                    if result.get('success'):
                        copy_info = result.get('copy_trade', {})
                        original_info = result.get('original_trade', {})
                        side = copy_info.get('side', 'BUY')
                        outcome = original_info.get('outcome', trade.get('outcome', 'YES'))
                        msg = (
                            f"🤖 **Copy Trade Executed!**\n\n"
                            f"**Market:** {original_info.get('market', 'Unknown')[:50]}\n"
                            f"**Action:** {side} {outcome}\n"
                            f"**Amount:** ${copy_info.get('usdc_amount', 0):.2f}\n"
                            f"**Order ID:** `{result.get('order_id', 'N/A')}`\n\n"
                            f"_Copied from tracked trader_"
                        )
                        if self.notification_queue:
                            await self.notification_queue.enqueue(user_id=user_id, message=msg, priority=7)
                        else:
                            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode='Markdown')
                        logger.info(f"✅ Copy trade success for user {user_id}")

                    elif result.get('skipped'):
                        reason = result.get('reason', 'Unknown reason')
                        logger.info(f"⏭️ Copy trade skipped for user {user_id}: {reason}")
                        side = trade.get('side', 'BUY')
                        outcome = trade.get('outcome', 'YES')
                        continue

                    elif result.get('error'):
                        raw_error = result.get('error', 'Unknown error')
                        logger.error(f"❌ Copy trade failed for user {user_id}: {raw_error}")
                        side = trade.get('side', 'BUY')
                        outcome = trade.get('outcome', 'YES')
                        friendly_error = parse_error_message(raw_error)
                        msg = (
                            f"⚠️ **Copy Trade Failed**\n\n"
                            f"**Market:** {trade.get('title', 'Unknown')[:50]}\n"
                            f"**Action:** {side} {outcome}\n"
                            f"**Reason:** {friendly_error}\n\n"
                            f"_Check your wallet balance and settings_"
                        )
                        keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton("📊 View Dashboard", callback_data="menu_main")]
                        ])
                        if self.notification_queue:
                            await self.notification_queue.enqueue(
                                user_id=user_id, message=msg, reply_markup=keyboard, priority=8
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=user_id, text=msg, parse_mode='Markdown', reply_markup=keyboard
                            )

                except Exception as e:
                    logger.error(f"Failed to send copy trade result to user {user_id}: {e}")

        except Exception as e:
            logger.error(f"Error processing copy trades: {e}", exc_info=True)



    async def check_and_process_multibuy(self, trade: dict, wallet_address: str, context: CallbackContext):
        """Check for multi-buy scenario and send alerts / execute copy trades."""
        try:
            if trade.get('side', '').upper() != 'BUY':
                return

            await self.db.record_buy_for_multibuy(trade, wallet_address)

            market_id = trade.get('condition_id') or trade.get('market_slug') or trade.get('market_id')
            outcome = trade.get('outcome', 'YES').upper()

            if not market_id:
                logger.debug("No market_id for multi-buy check")
                return

            wallet_addresses = await self.db.get_multibuy_wallets(market_id, outcome, hours=1)
            if len(wallet_addresses) < 2:
                return

            # Deduplication check
            wallet_fingerprint = "_".join(sorted(wallet_addresses))
            cache_key = f"{market_id}:{outcome}:{wallet_fingerprint}"
            now = datetime.now(timezone.utc)

            if cache_key in self._multibuy_processed_cache:
                cached_time = self._multibuy_processed_cache[cache_key]
                if (now - cached_time).total_seconds() < 3600:
                    logger.debug("⏭️ Multi-buy already processed - skipping")
                    return

            logger.info(
                f"🔥 Multi-buy detected! {len(wallet_addresses)} wallets bought "
                f"{outcome} on market {market_id[:20]}..."
            )

            # Mark as processed
            self._multibuy_processed_cache[cache_key] = now

            market_title = trade.get('title') or trade.get('market', 'Unknown Market')
            market_slug = trade.get('market_slug') or trade.get('slug')
            recent_buys = await self.db.get_recent_buys_for_market(market_id, outcome, hours=1)

            await self._send_multibuy_alerts(
                market_id=market_id,
                market_title=market_title,
                market_slug=market_slug,
                outcome=outcome,
                wallet_addresses=wallet_addresses,
                recent_buys=recent_buys,
                context=context
            )

            await self._execute_multibuy_copy_trades(
                trade=trade,
                market_title=market_title,
                outcome=outcome,
                wallet_addresses=wallet_addresses,
                context=context
            )

        except Exception as e:
            logger.error(f"Error in check_and_process_multibuy: {e}", exc_info=True)

    async def _send_multibuy_alerts(
        self,
        market_id: str,
        market_title: str,
        outcome: str,
        wallet_addresses: list,
        recent_buys: list,
        context: CallbackContext,
        market_slug: str = None,
    ):
        """Send multi-buy alerts to users who track 2+ of the buying wallets."""
        try:
            # Find users who track at least 2 of the wallets involved in the multi-buy
            user_wallet_count = Counter()
            for addr in wallet_addresses:
                ids = await self.db.get_users_tracking_wallet(addr)
                for uid in ids:
                    user_wallet_count[uid] += 1

            users = [uid for uid, count in user_wallet_count.items() if count >= 2]
            if not users:
                logger.debug("No users track 2+ of the multi-buy wallets — skipping alert")
                return

            wallet_summary = []
            for i, wallet in enumerate(wallet_addresses[:5], 1):
                buy = next((b for b in recent_buys if b['wallet_address'] == wallet), None)
                amount = buy.get('usdc_size', 0) if buy else 0
                short = f"{wallet[:6]}...{wallet[-4:]}"
                wallet_summary.append(f"{i}) `{short}` — ${amount:.2f}")

            wallets_text = "\n".join(wallet_summary)
            if len(wallet_addresses) > 5:
                wallets_text += f"\n... and {len(wallet_addresses) - 5} more"

            # Compute average price from recent_buys
            total_value_all = sum(float(b.get('usdc_size', 0)) for b in recent_buys)
            total_shares_all = sum(
                float(b.get('usdc_size', 0)) / float(b.get('price', 1))
                for b in recent_buys if float(b.get('price', 0)) > 0
            )
            avg_price_all = total_value_all / total_shares_all if total_shares_all > 0 else 0
            avg_price_line = f"**Average Price:** {format_price_cents(avg_price_all)}\n" if avg_price_all > 0 else ""

            # Build market line with optional link
            if market_slug:
                market_line = f"**Market:** {market_title} [(view)](https://polymarket.com/event/{market_slug})\n"
            else:
                market_line = f"**Market:** {market_title}\n"

            message = (
                f"🔥 **Multi-Buy Alert!**\n\n"
                f"{market_line}"
                f"**Outcome:** {outcome}\n"
                f"{avg_price_line}"
                f"**Recent Buys:**\n{wallets_text}\n\n"
            )

            for user_id in users:
                try:
                    # Use the notification queue for rate limiting + retry
                    if self.notification_queue:
                        await self.notification_queue.enqueue(
                            user_id=user_id, message=message, priority=6
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=user_id, text=message, parse_mode='Markdown'
                        )
                    await asyncio.sleep(0.05)
                except Exception as e:
                    logger.error(f"Failed to send alert to {user_id}: {e}")

            logger.info(f"Sent multi-buy alerts to {len(users)} users")
        except Exception as e:
            logger.error(f"Error sending multi-buy alerts: {e}")

    async def _execute_multibuy_copy_trades(
        self,
        trade: dict,
        market_title: str,
        outcome: str,
        wallet_addresses: list,
        context: CallbackContext
    ):
        """Execute copy trades for multi-buy — delegates to copy_manager for per-user logic."""
        try:
            results = await self.copy_manager.process_multibuy_copy_trades(
                trade=trade,
                wallet_addresses=wallet_addresses,
                context=context
            )

            for result in results:
                user_id = result.get('user_id')
                if not user_id:
                    continue
                try:
                    # Build wallet display lines per user (available to all branches below)
                    wallet_lines = []
                    for addr in wallet_addresses[:5]:
                        name = await self.db.get_wallet_display_name(user_id, addr)
                        if name and name != addr:
                            wallet_lines.append(f"{name} (`{addr}`)")
                    if len(wallet_addresses) > 5:
                        wallet_lines.append(f"_...and {len(wallet_addresses) - 5} more_")

                    if result.get('success'):
                        copy_trade_info = result.get('copy_trade', {})
                        copy_amount = copy_trade_info.get('usdc_amount', 0)
                        copy_price = copy_trade_info.get('price') or trade.get('price')
                        copy_price = float(copy_price) if copy_price is not None else None
                        logger.info(f"   ✅ Multi-buy copy succeeded for user {user_id}")
                        message = format_multibuy_copy_notification(
                            market_title=market_title,
                            outcome=outcome,
                            wallet_count=len(wallet_addresses),
                            copy_amount=copy_amount,
                            order_id=result.get('order_id'),
                            success=True,
                            wallet_lines=wallet_lines,
                            price=copy_price,
                        )

                    elif result.get('skipped'):
                        reason = result.get('reason', 'Unknown reason')
                        logger.info(f"⏭️ Multibuy copy trade skipped for user {user_id}: {reason}")
                        continue

                    else:
                        raw_error = result.get('error') or 'Unknown error'
                        logger.error(f"❌ Multibuy copy trade failed for user {user_id}: {raw_error}")
                        friendly_error = parse_error_message(raw_error)
                        message = format_multibuy_copy_notification(
                            market_title=market_title,
                            outcome=outcome,
                            wallet_count=len(wallet_addresses),
                            copy_amount=0,
                            error=friendly_error,
                            success=False,
                            wallet_lines=wallet_lines
                        )

                    if self.notification_queue:
                        await self.notification_queue.enqueue(user_id=user_id, message=message, priority=6)
                    else:
                        await context.bot.send_message(
                            chat_id=user_id, text=message, parse_mode='Markdown'
                        )

                except Exception as e:
                    logger.error(f"Failed to send multi-buy copy result to user {user_id}: {e}")

        except Exception as e:
            logger.error(f"Error executing multi-buy copy trades: {e}", exc_info=True)
