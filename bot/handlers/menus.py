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
        """Show trading wallet page. Source can be a query (callback) or update (command)."""
        # Detect if called from a message command or a callback button
        is_callback = hasattr(source, 'edit_message_text')

        wallet = await self.wallet_manager.get_wallet(user_id)
        if not wallet:
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
            
        # Run all heavy fetches concurrently
        safe = wallet.get('safe_address')
        status_task = self.wallet_manager.get_wallet_status(user_id)
        balances_task = self.wallet_manager.get_balances(user_id)
        positions_task = (
            self.polymarket.get_open_positions(safe)
            if safe else asyncio.sleep(0, result=None) 
        )

        status, balances, raw_positions = await asyncio.gather(
            status_task, balances_task, positions_task,
            return_exceptions=True
        )

        # Handle any failures gracefully
        if isinstance(status, Exception):
            logger.error(f"Error fetching status: {status}")
            status = {'safe_address': safe or 'Not set', 'ready_to_trade': False}
        if isinstance(balances, Exception):
            logger.error(f"Error fetching balances: {balances}")
            balances = {}
        if isinstance(raw_positions, Exception):
            logger.error(f"Error fetching positions: {raw_positions}")
            raw_positions = None

        eoa = wallet["address"]
        safe = status.get("safe_address", "Not set")
        ready = status.get("ready_to_trade", False)
        status_text = "Ready to trade ✅" if ready else "⚠️ Setup required"

        # Build positions text
        # Default to "No open positions" whenever the safe is configured —
        # "Setup required" should only appear when we have no safe address at all.
        positions_text = "Setup required" if not (safe and safe != "Not set") else "No open positions"
        if safe and safe != "Not set" and raw_positions:
            try:
                positions = raw_positions  # Reuse already-fetched positions
                if positions and len(positions) > 0:
                    total_value = 0
                    position_list = []
                    for pos in positions:
                        try:
                            market_title = pos.get("title", "Unknown")[:80]
                            outcome = pos.get("outcome", "Unknown")
                            current_value = float(pos.get("currentValue", 0) or 0)
                            cash_pnl = float(pos.get("cashPnl", 0) or 0)
                            if current_value < 0.01:
                                continue
                            total_value += current_value
                            pnl_emoji = "🟢 " if cash_pnl >= 0 else "🔴 "
                            position_list.append({"market": market_title, "outcome": outcome, "value": current_value, "pnl": cash_pnl, "pnl_emoji": pnl_emoji})
                        except (ValueError, TypeError):
                            continue
                    position_list.sort(key=lambda x: x["value"], reverse=True)
                    if position_list:
                        positions_text = f"*{len(position_list)} open* | Total: ${total_value:.2f}\n\n"
                        for pos in position_list[:5]:
                            pnl_sign = "+" if pos['pnl'] >= 0 else "-"
                            abs_pnl = abs(pos['pnl'])
                            title = pos['market'][:60] + ("…" if len(pos['market']) > 60 else "")
                            positions_text += (
                                f"`{title}`\n"
                                f"  ↳ {pos['outcome']}  ·  ${pos['value']:.2f}  ·  {pos['pnl_emoji']}{pnl_sign}${abs_pnl:.2f}\n\n"
                            )
                        if len(position_list) > 5:
                            positions_text = positions_text.rstrip("\n") + f"\n_...and {len(position_list) - 5} more_"
                    else:
                        positions_text = "No open positions"
                else:
                    positions_text = "No open positions"
            except Exception as e:
                logger.error(f"Error fetching positions for {safe}: {e}", exc_info=True)
                positions_text = "Unable to load"

        # ── Markets won (redeemable positions) ──────────────────────
        markets_won_text = ""
        if safe and safe != "Not set":
            try:
                won = await self.wallet_manager.get_won_markets(user_id)
                markets = won.get("markets", []) if won.get("success") else []
                if markets:
                    markets_won_text = f"**Markets won:** {len(markets)}\n"
                    for m in markets[:3]:
                        title = escape_markdown(m["title"])[:55]
                        pnl = m["pnl"]
                        markets_won_text += f"• {title} — +${pnl:.2f}\n"
                    if len(markets) > 3:
                        markets_won_text += f"_...and {len(markets) - 3} more_\n"
                else:
                    markets_won_text = "**Markets won:** None yet.\n"
            except Exception as e:
                logger.warning(f"Could not fetch won markets for user {user_id}: {e}")
                markets_won_text = "**Markets won:** Unable to load.\n"
        else:
            markets_won_text = "**Markets won:** Setup required.\n"

        message = (
            f"💳 *Your Trading Wallet*\n\n"
            f"*Send native USDC.e (Polygon) to your Polymarket address to deposit.*\n\n"
            f"*Polymarket Address:*\n`{safe}`\n\n"
            # f"*On-chain Address:*\n`{eoa}`\n"
            f"*Status:* {status_text}\n\n"
            f"**Balances:**\n"
            f"• Polymarket USDC.e: ${balances.get('polymarket_usdc', 0):.2f} USDC\n\n"
            f"**Positions:** {positions_text}\n\n"
            f"{markets_won_text}"

        )

        keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="wallet_refresh")]]
        #if not ready:
        keyboard.append([InlineKeyboardButton("⚙️ Complete Setup (Gasless)", callback_data="wallet_setup")])

        keyboard.extend([
            [InlineKeyboardButton("💸 Claim winnings", callback_data="wallet_claim")],
            [InlineKeyboardButton("📤 Withdraw USDC", callback_data="wallet_withdraw")],
            [InlineKeyboardButton("🗑️ Delete Wallet", callback_data="wallet_delete")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_main")],
        ])
        markup = InlineKeyboardMarkup(keyboard)

        if is_callback:
            await source.edit_message_text(message, parse_mode="Markdown", reply_markup=markup)
        else:
            await source.message.reply_text(message, parse_mode="Markdown", reply_markup=markup)

    
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
