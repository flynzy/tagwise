"""Menu display handlers for the Telegram bot."""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import asyncio

from bot.config import Config, TierLimits
from bot.services.database import WalletType
from bot.handlers.formatters import format_wallet_stats, escape_markdown
from bot.keyboards import get_main_menu_keyboard, get_wallet_tracker_keyboard

logger = logging.getLogger(__name__)


class MenuHandlers:
    """Handles menu display logic."""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.polymarket = bot.polymarket
        self.wallet_manager = bot.wallet_manager
        self.copy_manager = bot.copy_manager
    
    async def get_main_menu_text(self, user_id: int = None, is_pro: bool = None) -> str:
        from bot.config import Config
        
        if is_pro is None:
            is_pro = await self.db.is_pro(user_id) if user_id else False
        tier_badge = "💎 PRO" if is_pro else "🆓 Free"
        
        return f"""
🎯 **Welcome to Tagwise!**

Track top Polymarket traders and get real-time notifications of their trades.

**Your Status:** {tier_badge}

🌐 [Website]({Config.WEBSITE_URL}) • 𝕏 [Follow Us]({Config.X_URL})
        """


    async def show_wallet_tracker_menu(self, query, user_id: int):
        """Show the wallet tracker submenu."""
        tracked_wallets = await self.db.get_tracked_wallets(user_id)
        custom_count = len([w for w in tracked_wallets if w.get('wallet_type') == WalletType.CUSTOM.value])
        tagwise_count = len([w for w in tracked_wallets if w.get('wallet_type') == WalletType.TAGWISE.value])
        
        text = f"""
📊 **Wallet Tracker**

Track Polymarket wallets and get notified of their trades.

**Currently Tracking:**
• Custom Wallets: {custom_count}
• Leaderboard Traders: {tagwise_count}
"""
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=get_wallet_tracker_keyboard()
        )

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
        """Show the main menu."""
        user_id = update.effective_user.id
        
        # Clear any pending input states
        self.bot.awaiting_wallet_input.discard(user_id)
        
        is_pro = await self.db.is_pro(user_id)
        text = await self.get_main_menu_text(user_id, is_pro=is_pro)
        keyboard = get_main_menu_keyboard(is_pro)
        
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(
                text,
                parse_mode='Markdown',
                reply_markup=keyboard,
                disable_web_page_preview=True
            )
        else:
            message = update.message or update.callback_query.message
            await message.reply_text(
                text,
                parse_mode='Markdown',
                reply_markup=keyboard,
                disable_web_page_preview=True
            )
    
    async def show_wallets_page(self, query, user_id: int):
        """Show user's tracked wallets."""
        wallets = await self.db.get_tracked_wallets(user_id)
        
        if not wallets:
            keyboard = [
                [InlineKeyboardButton("➕ Track a Wallet", callback_data="menu_track")],
                [InlineKeyboardButton("🏆 Track from Leaderboard", callback_data="menu_toptraders")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]
            await query.edit_message_text(
                "📭 **No Tracked Wallets**\n\n"
                "You're not tracking any wallets yet.\n\n"
                "Choose an option to start tracking:",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        custom_wallets = [w for w in wallets if w.get('wallet_type') == WalletType.CUSTOM.value]
        tagwise_wallets = [w for w in wallets if w.get('wallet_type') == WalletType.TAGWISE.value]
        
        response = "📊 **Your Tracked Wallets**\n\n"
        
        if custom_wallets:
            response += "**📌 Custom Wallets:**\n\n"
            for wallet in custom_wallets:
                address = wallet['address']
                display_name = wallet.get('custom_name') or wallet.get('name') or f"{address[:6]}...{address[-4:]}"
                response += f"• **{display_name}**\n  `{address}`\n\n"
        
        if tagwise_wallets:
            response += f"**⭐ Leaderboard Traders ({len(tagwise_wallets)}):**\n\n"
            tagwise_wallets.sort(key=lambda w: w.get('leaderboard_rank') or 999)
            
            for wallet in tagwise_wallets[:8]:
                address = wallet['address']
                name = wallet.get('name') or f"{address[:6]}...{address[-4:]}"
                rank = wallet.get('leaderboard_rank')
                pnl = wallet.get('total_pnl', 0)
                rank_str = f"#{rank}" if rank else ""
                pnl_str = f"  PnL: ${pnl:,.0f}" if pnl else ""
                rank_display = f"  Rank: {rank_str}" if rank_str else ""
                response += f"• **{name}**{rank_display}{pnl_str}\n  `{address}`\n\n"
            
            if len(tagwise_wallets) > 8:
                response += f"\n_...and {len(tagwise_wallets) - 8} more_\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Track More", callback_data="menu_track")],
            [
                InlineKeyboardButton("🗑️ Untrack All Leaderboard", callback_data="untrack_leaderboard"),
                InlineKeyboardButton("🗑️ Untrack All Custom", callback_data="untrack_all"),
            ],
            [InlineKeyboardButton("🔄 Refresh", callback_data="menu_wallets")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
        ]
        
        await query.edit_message_text(
            response,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def show_track_instructions(self, query):
        """Show instructions for tracking a custom wallet."""
        keyboard = [
            [InlineKeyboardButton("🏆 Track from Leaderboard", callback_data="menu_toptraders")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
        ]
        
        await query.edit_message_text(
            "➕ **Track a Custom Wallet**\n\n"
            "To track a specific wallet, send a command:\n\n"
            "`/track <wallet_address>`\n"
            "`/track <wallet_address> <custom_name>`\n\n"
            "**Example:**\n"
            "`/track 0x1234...5678 WhaleTrader`\n\n"
            "The wallet address should:\n"
            "• Start with `0x`\n"
            "• Be 42 characters long\n\n"
            "_You can also use /name to rename tracked wallets_",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def show_analyze_page(self, query, user_id: int):
        """Show analyze wallet page."""
        self.bot.awaiting_wallet_input.add(user_id)
        
        keyboard = [
            [InlineKeyboardButton("❌ Cancel", callback_data="menu_main")],
        ]
        
        await query.edit_message_text(
            "🔍 <b>Analyze Wallet [Beta]</b>\n\n"
            "Get detailed statistics for any Polymarket wallet.\n\n"
            "<b>Send me a wallet address</b> to analyze:\n\n"
            "<i>The address should start with <code>0x</code> and be 42 characters long.</i>\n\n"
            "<b>Example:</b>\n"
            "<code>0x1234567890abcdef1234567890abcdef12345678</code>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def show_trading_wallet(self, source, user_id: int, context=None):
        """Show trading wallet page with list of all wallets. Source can be a query or update."""
        is_callback = hasattr(source, 'edit_message_text')

        wallets = await self.wallet_manager.get_wallets(user_id)
        if not wallets:
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data="wallet_refresh")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_main")],
            ]
            text = (
                "💳 *Trading Wallet*\n\n"
                "⏳ Your wallet is being set up in the background.\n\n"
                "This usually takes 30–60 seconds on first use.\n"
                "Press **Refresh** in a moment to check again."
            )
            markup = InlineKeyboardMarkup(keyboard)
            if is_callback:
                await source.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
            else:
                await source.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
            return

        # Fetch balances for all wallets concurrently
        balance_tasks = []
        for w in wallets:
            # Use the safe_address to fetch polymarket balance via positions API stub
            balance_tasks.append(self.wallet_manager.get_balances_for_wallet(w))
        balances_list = await asyncio.gather(*balance_tasks, return_exceptions=True)

        text = "*💳 Your Trading Wallets*\n\n"
        text += "_Send native USDC.e (Polygon) to deposit._\n\n"

        keyboard = []
        for i, w in enumerate(wallets):
            bal = balances_list[i]
            usdc = bal.get('polymarket_usdc', 0.0) if isinstance(bal, dict) else 0.0
            wname = w.get('wallet_name') or f"Wallet {w['wallet_index']}"
            active_marker = " ✅" if w.get('is_active') else ""
            safe = w.get('safe_address') or 'Not set'
            if safe and safe != 'Not set':
                poly_url = f"https://polymarket.com/profile/{safe}"
                text += f"*{wname}*{active_marker}\n`{safe}` — ${usdc:.2f} USDC ([view]({poly_url}))\n\n"
            else:
                text += f"*{wname}*{active_marker}\n`{safe}` — ${usdc:.2f} USDC\n\n"

        # Action buttons in order: Refresh → Portfolio → Withdraw → Set Active → Add → Delete → Back
        keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="wallet_refresh")])
        keyboard.append([InlineKeyboardButton("📊 Portfolio", callback_data="wallet_portfolio_pick")])
        keyboard.append([InlineKeyboardButton("📤 Withdraw USDC", callback_data="wallet_withdraw_pick")])
        if len(wallets) > 1:
            keyboard.append([InlineKeyboardButton("🔑 Set Active Wallet", callback_data="wallet_setactive")])
        if len(wallets) < 3:
            keyboard.append([InlineKeyboardButton("➕ Add Wallet", callback_data="wallet_add")])
        keyboard.extend([
            [InlineKeyboardButton("🗑️ Delete a Wallet", callback_data="wallet_delete_pick")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_main")],
        ])
        markup = InlineKeyboardMarkup(keyboard)

        if is_callback:
            await source.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        else:
            await source.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)

    async def show_portfolio(self, source, user_id: int, wallet_db_id: int = None):
        """Show detailed portfolio statistics for a specific wallet (or active wallet)."""
        is_callback = hasattr(source, 'edit_message_text')

        if wallet_db_id:
            wallet = await self.wallet_manager.get_wallet_by_id(user_id, wallet_db_id)
        else:
            wallet = await self.wallet_manager.get_wallet(user_id)
            if wallet:
                wallet_db_id = wallet.get('id')

        if not wallet:
            keyboard = [[InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_trading_wallet")]]
            text = "❌ No wallet found. Set up your trading wallet first."
            if is_callback:
                await source.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await source.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        safe = wallet.get('safe_address')
        wname = wallet.get('wallet_name') or f"Wallet {wallet.get('wallet_index', 1)}"

        if not safe:
            keyboard = [[InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_trading_wallet")]]
            text = "⚠️ Wallet setup not complete. Your Polymarket address is not ready yet."
            if is_callback:
                await source.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await source.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        # Fetch all data concurrently — use positions summary for accurate categorization
        balances_task = self.wallet_manager.get_balances_for_wallet(wallet)
        stats_task = self.polymarket.get_wallet_stats(safe)
        positions_task = self.wallet_manager.get_positions_summary_for_wallet(wallet)

        balances, stats, positions_summary = await asyncio.gather(
            balances_task, stats_task, positions_task,
            return_exceptions=True
        )

        if isinstance(balances, Exception):
            logger.error(f"Portfolio: error fetching balances: {balances}")
            balances = {}
        if isinstance(stats, Exception):
            logger.error(f"Portfolio: error fetching stats: {stats}")
            stats = {}
        if isinstance(positions_summary, Exception):
            logger.error(f"Portfolio: error fetching positions: {positions_summary}")
            positions_summary = {"open": [], "won": [], "lost": [], "success": False}

        usdc_balance = balances.get('polymarket_usdc', 0.0)
        total_pnl = stats.get('pnl_all_time', 0.0)
        realized_pnl = stats.get('realized_pnl', 0.0)
        open_pnl = stats.get('open_pnl', 0.0)
        roi = stats.get('roi_all_time', 0.0)
        win_rate = stats.get('win_rate')
        winning = stats.get('winning_positions', 0)
        losing = stats.get('losing_positions', 0)
        total_trades = stats.get('total_trades', 0)
        total_positions = stats.get('total_positions', 0)
        volume_7d = stats.get('volume_7d', 0.0)

        open_positions = positions_summary.get('open', [])
        won_positions = positions_summary.get('won', [])
        lost_positions = positions_summary.get('lost', [])
        open_count = len(open_positions)
        won_count = len(won_positions)
        lost_count = len(lost_positions)

        def fmt_pnl(v: float) -> str:
            sign = "+" if v >= 0 else "-"
            return f"{sign}${abs(v):.2f}"

        win_rate_str = f"{win_rate:.1f}%" if win_rate is not None else "N/A"
        roi_str = f"{roi:+.2f}%"
        roi_emoji = "🟢" if roi >= 0 else "🔴"
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"

        message = (
            f"📊 *{wname} Portfolio*\n\n"
            f"💰 *Balance*\n"
            f"├ USDC: ${usdc_balance:.2f}\n"
            f"└ Positions Value: ${open_pnl:.2f}\n\n"
            f"💸 *Profit & Loss*\n"
            f"├ All-Time: {pnl_emoji} {fmt_pnl(total_pnl)}\n"
            f"├ ROI: {roi_emoji} {roi_str}\n"
            f"├ Realized: {fmt_pnl(realized_pnl)}\n"
            f"└ Unrealized: {fmt_pnl(open_pnl)}\n\n"
            f"📈 *Trading Stats*\n"
            f"├ Win Rate: {win_rate_str}\n"
            f"├ Won: {winning} | Lost: {losing}\n"
            f"├ Trades: {total_trades:,}\n"
            f"├ 7d Volume: ${volume_7d:,.2f}\n"
            f"└ Markets Traded: {total_positions:,}\n\n"
            f"📂 *Positions*\n"
            f"├ Open: {open_count}\n"
            f"├ Lost: {lost_count}\n"
            f"└ Claimable (Won): {won_count}\n"
        )

        refresh_cb = f"wallet_portfolio_{wallet_db_id}" if wallet_db_id else "wallet_portfolio"
        keyboard = [
            [
                InlineKeyboardButton("📋 Open", callback_data=f"wallet_positions_{wallet_db_id}"),
                InlineKeyboardButton("🏆 Won", callback_data=f"wallet_wonmarkets_{wallet_db_id}"),
                InlineKeyboardButton("❌ Lost", callback_data=f"wallet_lostmarkets_{wallet_db_id}"),
            ],
            [InlineKeyboardButton("💸 Claim Winnings", callback_data="wallet_claim")],
            [InlineKeyboardButton("🔄 Refresh", callback_data=refresh_cb)],
            [InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_trading_wallet")],
        ]
        markup = InlineKeyboardMarkup(keyboard)

        if is_callback:
            await source.edit_message_text(message, parse_mode="Markdown", reply_markup=markup)
        else:
            await source.message.reply_text(message, parse_mode="Markdown", reply_markup=markup)

    async def show_open_positions(self, source, user_id: int, wallet_db_id: int):
        """Show open positions for a specific wallet."""
        is_callback = hasattr(source, 'edit_message_text')
        wallet = await self.wallet_manager.get_wallet_by_id(user_id, wallet_db_id)
        if not wallet or not wallet.get('safe_address'):
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=f"wallet_portfolio_{wallet_db_id}")]]
            msg = "⚠️ Wallet not found or not set up."
            if is_callback:
                await source.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        safe = wallet['safe_address']
        wname = wallet.get('wallet_name') or f"Wallet {wallet.get('wallet_index', 1)}"

        try:
            raw_positions = await self.polymarket.get_open_positions(safe)
        except Exception as e:
            raw_positions = None
            logger.error(f"Error fetching positions: {e}")

        keyboard = [[InlineKeyboardButton("🔙 Back to Portfolio", callback_data=f"wallet_portfolio_{wallet_db_id}")]]

        if not raw_positions:
            msg = f"📋 *{wname} — Open Positions*\n\nNo open positions."
            if is_callback:
                await source.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        total_value = 0
        position_list = []
        for pos in raw_positions:
            try:
                current_value = float(pos.get("currentValue", 0) or 0)
                if current_value < 0.01:
                    continue
                cash_pnl = float(pos.get("cashPnl", 0) or 0)
                total_value += current_value
                pnl_emoji = "🟢" if cash_pnl >= 0 else "🔴"
                pnl_sign = "+" if cash_pnl >= 0 else "-"
                position_list.append({
                    "market": pos.get("title", "Unknown")[:60],
                    "outcome": pos.get("outcome", ""),
                    "value": current_value,
                    "pnl": cash_pnl,
                    "pnl_emoji": pnl_emoji,
                    "pnl_sign": pnl_sign,
                })
            except (ValueError, TypeError):
                continue

        position_list.sort(key=lambda x: x["value"], reverse=True)

        msg = f"📋 *{wname} — Open Positions*\n"
        msg += f"_{len(position_list)} open | Total: ${total_value:.2f}_\n\n"
        for pos in position_list[:10]:
            title = pos['market'] + ("…" if len(pos['market']) >= 60 else "")
            msg += f"`{title}`\n  ↳ {pos['outcome']} · ${pos['value']:.2f} · {pos['pnl_emoji']}{pos['pnl_sign']}${abs(pos['pnl']):.2f}\n\n"
        if len(position_list) > 10:
            msg += f"_...and {len(position_list) - 10} more_\n"

        if is_callback:
            await source.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await source.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    async def show_won_markets(self, source, user_id: int, wallet_db_id: int):
        """Show claimable won markets for a specific wallet."""
        is_callback = hasattr(source, 'edit_message_text')
        wallet = await self.wallet_manager.get_wallet_by_id(user_id, wallet_db_id)
        if not wallet or not wallet.get('safe_address'):
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=f"wallet_portfolio_{wallet_db_id}")]]
            msg = "⚠️ Wallet not found or not set up."
            if is_callback:
                await source.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        wname = wallet.get('wallet_name') or f"Wallet {wallet.get('wallet_index', 1)}"

        try:
            won_result = await self.wallet_manager.get_won_markets_for_wallet(wallet)
        except Exception as e:
            logger.error(f"Error fetching won markets: {e}")
            won_result = {"success": False, "markets": []}

        keyboard = [
            [InlineKeyboardButton("💸 Claim All", callback_data="wallet_claim")],
            [InlineKeyboardButton("🔙 Back to Portfolio", callback_data=f"wallet_portfolio_{wallet_db_id}")],
        ]

        markets = won_result.get("markets", [])
        if not markets:
            msg = f"🏆 *{wname} — Markets Won*\n\nNo claimable markets at this time."
            if is_callback:
                await source.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        msg = f"🏆 *{wname} — Markets Won* ({len(markets)} claimable)\n\n"
        for m in markets[:10]:
            title = escape_markdown(m['title'])[:60]
            msg += f"✅ {title}\n  └ +${m['pnl']:.2f} USDC\n\n"
        if len(markets) > 10:
            msg += f"_...and {len(markets) - 10} more_\n"

        if is_callback:
            await source.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await source.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    
    async def show_lost_markets(self, source, user_id: int, wallet_db_id: int):
        """Show resolved lost markets for a specific wallet."""
        is_callback = hasattr(source, 'edit_message_text')
        wallet = await self.wallet_manager.get_wallet_by_id(user_id, wallet_db_id)
        if not wallet or not wallet.get('safe_address'):
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=f"wallet_portfolio_{wallet_db_id}")]]
            msg = "⚠️ Wallet not found or not set up."
            if is_callback:
                await source.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        wname = wallet.get('wallet_name') or f"Wallet {wallet.get('wallet_index', 1)}"

        try:
            lost_result = await self.wallet_manager.get_lost_markets_for_wallet(wallet)
        except Exception as e:
            logger.error(f"Error fetching lost markets: {e}")
            lost_result = {"success": False, "markets": []}

        keyboard = [[InlineKeyboardButton("🔙 Back to Portfolio", callback_data=f"wallet_portfolio_{wallet_db_id}")]]

        markets = lost_result.get("markets", [])
        if not markets:
            msg = f"❌ *{wname} — Markets Lost*\n\nNo resolved lost markets found."
            if is_callback:
                await source.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        msg = f"❌ *{wname} — Markets Lost* ({len(markets)})\n\n"
        for m in markets[:10]:
            title = escape_markdown(m['title'])[:60]
            pnl = m.get('pnl', 0.0)
            pnl_str = f"{pnl:+.2f}" if pnl else "−$0.00"
            msg += f"❌ {title}\n  └ {pnl_str} USDC\n\n"
        if len(markets) > 10:
            msg += f"_...and {len(markets) - 10} more_\n"

        if is_callback:
            await source.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await source.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    async def show_copytrade_page(self, query, user_id: int):
        """Show copy trading page - delegates to TradingCommands."""
        # Just redirect to the trading commands version
        from bot.trading.commands import TradingCommands
        await self.bot.trading_commands._show_copytrade_main(query, user_id, edit=True)
    
    async def show_account_page(self, query, user_id: int):
        """Show account page."""
        username = query.from_user.username or "N/A"
        message, keyboard = await self.bot.displays.render_account_view(user_id, username)
        
        await query.edit_message_text(
            message,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

    async def show_upgrade_page(self, query, user_id: int):
        """Show upgrade page with payment links."""
        message, keyboard = await self.bot.displays.render_upgrade_view(user_id)
        
        await query.edit_message_text(
            message,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

    # ✅ NEW: Referral page
    async def show_referral_page(self, query, user_id: int):
        """Show referral & rewards page."""
        message, keyboard = await self.bot.displays.render_referral_view(user_id)
        
        await query.edit_message_text(
            message,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

    async def show_welcome_screen(self, update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
        """Show the initial welcome/landing page with links."""
        from bot.config import Config
        
        text = """
🎯 **Welcome to Tagwise!**

Consensus-driven copy trading for Polymarket.

**What is Tagwise?**
Track top Polymarket traders, get real-time alerts, and copy winning trades, all from Telegram.

**Key Features:**
• 🔔 Real-time trade notifications
• 📊 Trader performance analytics
• 🤖 Automated copy trading
• 🏆 Leaderboard tracking
• 💎 Multi Buy Alerts & Confidence scoring (PRO)

Ready to start tracking the smartest traders on Polymarket?
    """
        
        keyboard = [
            [InlineKeyboardButton("🚀 Get Started", callback_data="welcome_start")],
            [
                InlineKeyboardButton("🌐 Website", url=Config.WEBSITE_URL),
                InlineKeyboardButton("𝕏 Follow Us", url=Config.X_URL)
            ],
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(
                text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            message = update.message or (update.callback_query.message if update.callback_query else None)
            if message:
                await message.reply_text(
                    text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )


    def _get_help_text(self) -> str:
        """Returns the help page text."""
        return """
    📖 *Tagwise — Command Reference*

    *📡 Wallet Tracking*
    • `/track <address>` — Start tracking a wallet
    • `/track <address> <name>` — Track with a custom label
    • `/track <period> <category>` — Track top 5 leaderboard traders
    • `/wallets` — View all your tracked wallets
    • `/untrack <address>` — Stop tracking a specific wallet
    • `/untrack leaderboard` — Remove all leaderboard traders
    • `/untrack all` — Remove all tracked wallets
    • `/name <address> <name>` — Rename a tracked wallet

    *🏆 Leaderboard*
    • `/toptraders` — View top Polymarket traders
    • `/toptraders <period> <category>` — Filter leaderboard results

    *🔍 Analytics*
    • `/stats <address>` — Get stats for any wallet
    • *Analyze Wallet* button in the menu for interactive analysis

    *💳 Trading Wallet*
    • `/wallet` — View your on-chain trading wallet
    • `/copy` — Manage copy trading settings

    *⚙️ Account*
    • `/account` — View your subscription status
    • `/upgrade` — Upgrade to PRO
    • `/referral` — View your referral link & rewards
    • `/start` — Return to the main menu
    • `/help` — Show this page

    *📅 Valid Time Periods:*
    `all` · `1d` · `1w` · `1m` · `3m` · `6m` · `1y`

    *🗂️ Valid Categories:*
    `overall` · `sports` · `politics` · `crypto` · `tech` · `culture`
    """

    async def show_help_page(self, query):
        """Show the help page with a list of all available commands."""
        keyboard = [
            [InlineKeyboardButton("🏠 Back to Menu", callback_data="menu_main")],
        ]

        await query.edit_message_text(
            self._get_help_text(),
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
