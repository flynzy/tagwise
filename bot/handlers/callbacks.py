"""Callback query handlers for the Telegram bot."""
import asyncio 
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.config import Config, TierLimits
from bot.services.database import WalletType
from bot.constants import (
    TIME_PERIOD_DISPLAY, CATEGORY_DISPLAY, LEADERBOARD_TRACK_LIMIT
)
from bot.handlers.formatters import format_wallet_stats, format_top_trader, escape_markdown
from bot.keyboards import (
    get_toptraders_category_keyboard,
    get_time_period_keyboard,
    get_leaderboard_results_keyboard,
)

logger = logging.getLogger(__name__)


class CallbackHandlers:
    """Handles all callback query interactions."""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.polymarket = bot.polymarket
        self.copy_manager = bot.copy_manager

    async def welcome_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle welcome screen callbacks."""
        query = update.callback_query
        await query.answer()
        
        if query.data == "welcome_start":
            # Show main menu after user clicks "Get Started"
            await self.bot.menu_handlers.show_main_menu(update, context, edit=True)
    
    # Pages that need DB/API calls and benefit from an instant loading state
    _HEAVY_PAGES = {"wallet_tracker", "wallets", "account", "upgrade", "trading_wallet", "copytrade", "referral"}

    async def menu_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle main menu callback buttons."""
        query = update.callback_query

        action = query.data.replace("menu_", "")
        user_id = update.effective_user.id

        # Clear any pending input states when navigating
        self.bot.awaiting_wallet_input.discard(user_id)

        # Show instant loading for heavy pages so the UI feels responsive
        if action in self._HEAVY_PAGES:
            await query.answer()
            await query.edit_message_text("⏳ Loading...")
        else:
            await query.answer()

        menu_handlers = self.bot.menu_handlers

        if action == "main":
            await menu_handlers.show_main_menu(update, context, edit=True)
        elif action == "wallet_tracker":
            await menu_handlers.show_wallet_tracker_menu(query, user_id)
        elif action == "toptraders":
            await self._show_toptraders_category_menu(query)
        elif action == "wallets":
            await menu_handlers.show_wallets_page(query, user_id)
        elif action == "track":
            await menu_handlers.show_track_instructions(query)
        elif action == "analyze":
            await menu_handlers.show_analyze_page(query, user_id)
        elif action == "trading_wallet":
            await menu_handlers.show_trading_wallet(query, user_id)
        elif action == "copytrade":
            await menu_handlers.show_copytrade_page(query, user_id)
        elif action == "account":
            await menu_handlers.show_account_page(query, user_id)
        elif action == "upgrade":
            await menu_handlers.show_upgrade_page(query, user_id)
        elif action == "help":
            await menu_handlers.show_help_page(query)
        elif action == "referral":
            await menu_handlers.show_referral_page(query, user_id)
        
    async def _show_toptraders_category_menu(self, query):
        """Show top traders category selection (Step 1)."""
        await query.edit_message_text(
            "🏆 **Top Traders**\n\n"
            "**Step 1:** Select a category\n\n"
            "_Choose a market category to view top performers:_",
            parse_mode='Markdown',
            reply_markup=get_toptraders_category_keyboard()
        )
    
    async def toptraders_category_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle category selection - show time period options (Step 2)."""
        query = update.callback_query
        await query.answer()
        
        category = query.data.replace("topcat_", "")
        category_display = CATEGORY_DISPLAY.get(category, '🌐 Overall')
        
        await query.edit_message_text(
            f"🏆 **Top Traders** | {category_display}\n\n"
            f"**Step 2:** Select a time period\n\n"
            f"_Choose how far back to look for top performers:_",
            parse_mode='Markdown',
            reply_markup=get_time_period_keyboard(category)
        )
    
    async def toptraders_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle top traders callback - show leaderboard results (Step 3)."""
        query = update.callback_query
        await query.answer()

        parts = query.data.split("_")
        if len(parts) != 3:
            return

        _, category, time_period = parts

        period_display = TIME_PERIOD_DISPLAY.get(time_period, 'All-Time')
        category_display = CATEGORY_DISPLAY.get(category, '🌐 Overall')

        await query.edit_message_text("⏳ Loading...")
        
        traders = await self.polymarket.get_leaderboard(
            limit=getattr(self.bot.config, 'LEADERBOARD_TOP_N', 10),
            time_period=time_period,
            category=category,
            order_by='PNL'
        )
        
        if not traders:
            keyboard = [
                [InlineKeyboardButton("🔄 Try Again", callback_data=f"top_{category}_{time_period}")],
                [InlineKeyboardButton("⬅️ Back to Categories", callback_data="menu_toptraders")],
            ]
            await query.edit_message_text(
                "❌ Unable to fetch leaderboard data. Please try again.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        response = f"🏆 **Top {len(traders)} Traders** | {category_display} | {period_display}\n\n"
        
        for i, trader in enumerate(traders[:10], 1):
            response += format_top_trader(i, trader)
            response += "\n"
        
        await query.edit_message_text(
            response,
            parse_mode='Markdown',
            reply_markup=get_leaderboard_results_keyboard(category, time_period)
        )
    
    async def track_leaderboard_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle tracking from leaderboard."""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        parts = query.data.split("_")
        if len(parts) != 3:
            return
        
        _, category, time_period = parts
        
        period_display = TIME_PERIOD_DISPLAY.get(time_period, 'All-Time')
        category_display = CATEGORY_DISPLAY.get(category, '🌐 Overall')
        
        await query.edit_message_text("⏳ Loading...")
        
        traders = await self.polymarket.get_leaderboard(
            limit=LEADERBOARD_TRACK_LIMIT,
            time_period=time_period,
            category=category,
            order_by='PNL'
        )
        
        if not traders:
            keyboard = [
                [InlineKeyboardButton("🔄 Try Again", callback_data=f"trackld_{category}_{time_period}")],
                [InlineKeyboardButton("⬅️ Back", callback_data=f"top_{category}_{time_period}")],
            ]
            await query.edit_message_text(
                "❌ Unable to fetch leaderboard data. Please try again.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        await self.db.update_leaderboard_wallets(traders)
        
        # Get current wallet count BEFORE adding
        currently_tracked = await self.db.get_tracked_wallets(user_id)
        initial_count = len(currently_tracked)
        
        # Try to add each trader
        new_count = 0
        
        for trader in traders:
            address = trader.get('address', '').lower()
            if not address:
                continue
            
            # Returns True if newly added, False if already existed
            success = await self.db.add_tracked_wallet(
                user_id=user_id,
                wallet_address=address,
                wallet_type=WalletType.TAGWISE.value,
                leaderboard_info=trader
            )
            
            if success:
                new_count += 1
        
        await self.db.set_leaderboard_subscription(user_id, True)
        
        # Calculate total: previous count + newly added
        total_tracked = initial_count + new_count
        
        # Build trader list with better name formatting
        trader_list = []
        for i, trader in enumerate(traders, 1):
            # Get name with better fallback logic
            display_name = trader.get('display_name', '').strip()
            username = (trader.get('username') or '').strip()
            address = (trader.get('address') or '').strip()
            
            # Check if display_name or username looks like an address (starts with 0x and is long)
            if display_name and not (display_name.startswith('0x') and len(display_name) > 20):
                name = display_name
            elif username and not (username.startswith('0x') and len(username) > 20):
                name = username
            else:
                # Use shortened address format
                name = f"{address[:7]}...{address[-4:]}" if len(address) >= 42 else address
            
            verified = " ✅" if trader.get('verified') else ""
            pnl = trader.get('pnl', 0)
            
            trader_list.append(f"{i}. {escape_markdown(name)}{verified} - `{address}`")
        
        keyboard = [
            [InlineKeyboardButton("📊 View Tracked Wallets", callback_data="menu_wallets")],
            [InlineKeyboardButton("🏆 View More Traders", callback_data="menu_toptraders")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
        ]
        
        await query.edit_message_text(
            f"✅ **Now Tracking Top {len(traders)} Traders!**\n\n"
            f"**Leaderboard:** {category_display} | {period_display}\n\n"
            f"**Traders:**\n{chr(10).join(trader_list)}\n\n"
            f"📊 **Newly added:** {new_count}\n"
            f"📝 **Total tracked:** {total_tracked}\n\n"
            f"🔔 You'll receive notifications when these traders make moves!",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.info(f"User {user_id} tracking result: {new_count} new, {total_tracked} total")
    
    async def track_wallet_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle tracking a wallet from analyze page."""
        query = update.callback_query
        await query.answer()
        
        wallet_address = query.data.replace("trackwallet_", "").lower()
        user_id = update.effective_user.id
        
        tracked_wallets = await self.db.get_tracked_wallets(user_id)
        is_pro = await self.db.is_pro(user_id)
        custom_count = len([w for w in tracked_wallets if w.get('wallet_type') == WalletType.CUSTOM.value])
        
        if not is_pro and custom_count >= TierLimits.FREE_MAX_CUSTOM_WALLETS:
            keyboard = [
                [InlineKeyboardButton("💎 Upgrade to PRO", callback_data="menu_upgrade")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]
            await query.edit_message_text(
                f"❌ You've reached the free tier limit of {TierLimits.FREE_MAX_CUSTOM_WALLETS} custom wallets.\n\n"
                f"Upgrade to PRO for unlimited wallet tracking!",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        success = await self.db.add_tracked_wallet(
            user_id=user_id,
            wallet_address=wallet_address,
            wallet_type=WalletType.CUSTOM.value
        )
        
        if success:
            keyboard = [
                [InlineKeyboardButton("📊 View My Wallets", callback_data="menu_wallets")],
                [InlineKeyboardButton("🔍 Analyze Another", callback_data="menu_analyze")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]
            await query.edit_message_text(
                f"✅ **Now Tracking!**\n\n"
                f"`{wallet_address}`\n\n"
                f"🔔 You'll receive notifications when this wallet makes trades!",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            keyboard = [
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]
            await query.edit_message_text(
                "❌ Failed to track wallet. Please try again.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    async def untrack_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle untrack callbacks."""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        action = query.data.replace("untrack_", "")
        
        if action == "leaderboard":
            removed_count = await self.db.remove_all_leaderboard_wallets(user_id)
            await self.db.set_leaderboard_subscription(user_id, False)
            
            keyboard = [
                [InlineKeyboardButton("📊 View Remaining", callback_data="menu_wallets")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]
            
            await query.edit_message_text(
                f"✅ **Removed Leaderboard Wallets**\n\n"
                f"Stopped tracking **{removed_count}** leaderboard trader(s).\n\n"
                f"Your custom wallets are still being tracked.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif action == "all":
            keyboard = [
                [InlineKeyboardButton("⚠️ Yes, Untrack All", callback_data="untrack_confirm_all")],
                [InlineKeyboardButton("❌ Cancel", callback_data="menu_wallets")],
            ]
            
            await query.edit_message_text(
                "⚠️ **Confirm Untrack All**\n\n"
                "Are you sure you want to stop tracking ALL wallets?\n\n"
                "This includes both custom wallets and leaderboard traders.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif action == "confirm_all":
            removed_count = await self.db.remove_all_wallets(user_id)
            await self.db.set_leaderboard_subscription(user_id, False)
            
            keyboard = [
                [InlineKeyboardButton("➕ Start Tracking", callback_data="menu_track")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]
            
            await query.edit_message_text(
                f"✅ **Removed All Wallets**\n\n"
                f"Stopped tracking **{removed_count}** wallet(s).\n\n"
                f"Use the buttons below to start tracking again.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    # ✅ NEW: Referral share callback
    async def referral_share_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle referral_share callback — shows copyable referral link."""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        referral_stats = await self.db.get_referral_stats(user_id)
        ref_code = referral_stats.get('referral_code')
        
        bot_username = Config.BOT_USERNAME if hasattr(Config, 'BOT_USERNAME') else "tagwise_bot"
        referral_link = f"https://t.me/{bot_username}?start=ref_{ref_code}"
        
        # Send as a separate message so user can easily copy
        await query.message.reply_text(
            f"📋 **Your Referral Link:**\n\n"
            f"`{referral_link}`\n\n"
            f"_Tap the link above to copy it, then share with friends!_\n\n"
            f"Your friend gets a **3-day free PRO trial**, and when they "
            f"subscribe you earn **7 extra days of PRO**.",
            parse_mode='Markdown'
        )
    
    async def noop_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle no-operation callbacks."""
        query = update.callback_query
        await query.answer("You're already tracking this wallet!", show_alert=False)

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages - used for wallet address input in analyze mode."""
        user_id = update.effective_user.id
        
        if user_id not in self.bot.awaiting_wallet_input:
            return
        
        text = update.message.text.strip().lower()
        
        if text.startswith('0x') and len(text) == 42:
            self.bot.awaiting_wallet_input.discard(user_id)
            await self._analyze_wallet(update, text, user_id)
        else:
            keyboard = [
                [InlineKeyboardButton("❌ Cancel", callback_data="menu_main")],
            ]
            await update.message.reply_text(
                "❌ **Invalid wallet address**\n\n"
                "Please send a valid wallet address:\n"
                "• Must start with `0x`\n"
                "• Must be 42 characters long\n\n"
                "_Try again or click Cancel to go back._",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    async def _analyze_wallet(self, update: Update, wallet_address: str, user_id: int):
        """Analyze a wallet and show statistics."""
        loading_msg = await update.message.reply_text("⏳ Analyzing wallet...")
        
        try:
            custom_name = None
            tracked = await self.db.get_tracked_wallets(user_id)
            for w in tracked:
                if w['address'].lower() == wallet_address:
                    custom_name = w.get('custom_name')
                    break
            
            stats = await self.polymarket.get_wallet_stats(wallet_address)
            response = format_wallet_stats(wallet_address, stats, custom_name)
            
            is_tracking = any(w['address'].lower() == wallet_address for w in tracked)
            
            keyboard = []
            if not is_tracking:
                keyboard.append([
                    InlineKeyboardButton("📌 Track This Wallet", callback_data=f"trackwallet_{wallet_address}")
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton("✅ Already Tracking", callback_data="noop")
                ])
            
            keyboard.extend([
                [InlineKeyboardButton("🔍 Analyze Another", callback_data="menu_analyze")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ])
            
            await loading_msg.edit_text(
                response,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        except Exception as e:
            logger.error(f"Error analyzing wallet: {e}", exc_info=True)
            keyboard = [
                [InlineKeyboardButton("🔄 Try Again", callback_data="menu_analyze")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]
            await loading_msg.edit_text(
                "❌ Failed to analyze wallet. Please try again.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
