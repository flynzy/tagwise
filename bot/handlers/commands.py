"""Command handlers for the Telegram bot."""

import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.config import Config, TierLimits
from bot.services.database import WalletType
from bot.constants import (
    TIME_PERIOD_MAP, TIME_PERIOD_DISPLAY,
    CATEGORY_MAP, CATEGORY_DISPLAY,
    LEADERBOARD_TRACK_LIMIT,
)
from bot.handlers.formatters import format_wallet_stats, format_top_trader, escape_markdown
from bot.keyboards import get_back_button
from bot.services.notifications import format_wallet_address
import asyncio

logger = logging.getLogger(__name__)


class CommandHandlers:
    """Handles all /command interactions."""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.polymarket = bot.polymarket
    
    def _parse_leaderboard_args(self, args: list) -> tuple[str, str, str, str]:
        """Parse time period and category from command arguments."""
        time_period = 'ALL'
        category = 'OVERALL'
        
        for arg in args:
            arg_lower = arg.lower()
            if arg_lower in TIME_PERIOD_MAP:
                time_period = TIME_PERIOD_MAP[arg_lower]
            elif arg_lower in CATEGORY_MAP:
                category = CATEGORY_MAP[arg_lower]
        
        period_display = TIME_PERIOD_DISPLAY.get(time_period, 'All-Time')
        category_display = CATEGORY_DISPLAY.get(category, '🌐 Overall')
        
        return time_period, period_display, category, category_display
    
    def _is_leaderboard_arg(self, arg: str) -> bool:
        """Check if an argument is a valid leaderboard filter."""
        arg_lower = arg.lower()
        return arg_lower in TIME_PERIOD_MAP or arg_lower in CATEGORY_MAP
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command - Show welcome screen for new users, process referral links."""
        user_id = update.effective_user.id
        
        # Check if this is a new user (never used the bot before)
        # Check both tracked wallets AND the referred_by field to prevent
        # users who deleted wallets from re-claiming a referral trial.
        tracked_wallets = await self.db.get_tracked_wallets(user_id)
        has_used_bot = len(tracked_wallets) > 0
        
        # Process referral deep link (format: /start ref_CODE)
        if context.args and context.args[0].startswith('ref_'):
            ref_code = context.args[0][4:]  # Strip 'ref_' prefix
            if ref_code and not has_used_bot:
                # Only process referral for genuinely new users
                success = await self.db.record_referral(ref_code, user_id)
                if success:
                    # Give referee a free 3-day PRO trial
                    trial_applied = await self.db.apply_referee_trial(user_id, trial_days=3)
                    if trial_applied:
                        await update.message.reply_text(
                            "🎁 **Welcome!** You've been referred by a Tagwise user!\n\n"
                            "You've received a **3-day PRO trial** as a welcome gift. "
                            "Enjoy unlimited tracking and all premium features!",
                            parse_mode='Markdown'
                        )
                    logger.info(f"Referral processed: code={ref_code}, new_user={user_id}")
        
        # Show welcome screen for new users, main menu for returning users
        if has_used_bot:
            await self.bot.menu_handlers.show_main_menu(update, context)
        else:
            await self.bot.menu_handlers.show_welcome_screen(update, context)

        asyncio.create_task(self.bot._auto_provision_wallet(user_id))
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command - Show the help page."""
        keyboard = [
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
        ]

        await update.message.reply_text(
            self.bot.menu_handlers._get_help_text(),
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def account_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /account command - Show user account status."""
        try:
            user_id = update.effective_user.id
            username = update.effective_user.username or "N/A"
            
            message, keyboard = await self.bot.displays.render_account_view(user_id, username)
            
            await update.message.reply_text(
                message, 
                parse_mode='Markdown',
                reply_markup=keyboard
            )
            
        except Exception as e:
            logger.error(f"Error in account_command: {e}", exc_info=True)
            await update.message.reply_text("❌ An error occurred. Please try again.")

    async def upgrade_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /upgrade command - Show upgrade options."""
        user_id = update.effective_user.id
        
        loading_msg = await update.message.reply_text("⏳ Generating payment links...")
        
        message, keyboard = await self.bot.displays.render_upgrade_view(user_id)
        
        await loading_msg.edit_text(
            message,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

    
    async def admin_activate_pro(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /activate command - Admin command to manually activate PRO."""
        try:
            admin_id = update.effective_user.id
            
            if admin_id not in Config.ADMIN_USER_IDS:
                await update.message.reply_text("❌ This command is admin-only.")
                return
            
            if len(context.args) < 1:
                await update.message.reply_text(
                    "❌ Usage: `/activate <user_id> [monthly|annual]`",
                    parse_mode='Markdown'
                )
                return
            
            target_user_id = int(context.args[0])
            plan_type = context.args[1] if len(context.args) > 1 else 'monthly'
            
            if plan_type not in ['monthly', 'annual']:
                plan_type = 'monthly'
            
            success = await self.db.upgrade_to_pro(
                user_id=target_user_id,
                subscription_type=plan_type,
                payment_method='admin_grant',
                payment_tx=f"admin_{admin_id}_{datetime.utcnow().timestamp()}",
                payment_amount=0
            )
            
            if success:
                await update.message.reply_text(
                    f"✅ Activated PRO ({plan_type}) for user `{target_user_id}`",
                    parse_mode='Markdown'
                )
                
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=f"""
 **Welcome to Tagwise PRO! 🎉**

Your {plan_type} subscription has been activated!

 **What's unlocked:**
• Confidence scores on every alert 🎯
• Unlimited wallet tracking 📊 
• Track ALL top traders ⭐
• Advanced leaderboard filters 🔍 
• Multi-buy alerts 🔊

Use /account to view your status.
Enjoy! 🚀
""",
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.warning(f"Could not notify user {target_user_id}: {e}")
            else:
                await update.message.reply_text("❌ Failed to activate PRO.")
                
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID. Must be a number.")
        except Exception as e:
            logger.error(f"Error in admin_activate_pro: {e}", exc_info=True)
            await update.message.reply_text("❌ An error occurred.")
    
    async def top_traders(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /toptraders command - Show current top traders."""
        try:
            time_period, period_display, category, category_display = self._parse_leaderboard_args(context.args or [])
            
            await update.message.reply_text(
                f"⏳ Fetching {period_display.lower()} top traders in {category_display}..."
            )
            
            traders = await self.polymarket.get_leaderboard(
                limit=getattr(Config, 'LEADERBOARD_TOP_N', 10),
                time_period=time_period,
                category=category,
                order_by='PNL'
            )
            
            if not traders:
                await update.message.reply_text(
                    "❌ Unable to fetch leaderboard data. Please try again later.",
                    reply_markup=get_back_button()
                )
                return
            
            response = f"🏆 **Top {len(traders)} Traders** | {category_display} | {period_display}\n\n"
            
            for i, trader in enumerate(traders, 1):
                response += format_top_trader(i, trader)
                response += "\n"
            
            keyboard = [
                [InlineKeyboardButton(f"📌 Track Top 5", callback_data=f"trackld_{category}_{time_period}")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]
            
            await update.message.reply_text(
                response, 
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        except Exception as e:
            logger.error(f"Error in top_traders: {e}", exc_info=True)
            await update.message.reply_text("❌ An error occurred. Please try again.")
    
    async def track_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /track command - Track a custom wallet or top traders."""
        try:
            if not context.args:
                keyboard = [
                    [InlineKeyboardButton("🏆 Track from Leaderboard", callback_data="menu_toptraders")],
                    [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
                ]
                await update.message.reply_text(
                    "➕ **Track a Wallet**\n\n"
                    "**Track a custom wallet:**\n"
                    "`/track <wallet_address>`\n"
                    "`/track <wallet_address> <custom_name>`\n\n"
                    "**Track top 5 from leaderboard:**\n"
                    "`/track <period> <category>`\n\n"
                    "**Examples:**\n"
                    "`/track 0x1234...5678 WhaleTrader`\n"
                    "`/track weekly sports`\n\n"
                    "Or use the button below:",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
            first_arg = context.args[0].strip().lower()
            
            if first_arg.startswith('0x') and len(first_arg) == 42:
                await self._track_custom_wallet(update, context)
            elif self._is_leaderboard_arg(first_arg) or (len(context.args) > 1 and self._is_leaderboard_arg(context.args[1].lower())):
                await self._track_leaderboard(update, context)
            else:
                await update.message.reply_text(
                    f"❌ Invalid argument: `{first_arg}`\n\n"
                    "Use a wallet address (starting with 0x) or leaderboard filters.",
                    parse_mode='Markdown',
                    reply_markup=get_back_button()
                )
                
        except Exception as e:
            logger.error(f"Error in track_wallet: {e}", exc_info=True)
            await update.message.reply_text("❌ An error occurred. Please try again.")
    
    async def _track_custom_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Track a custom wallet address."""
        wallet_address = context.args[0].strip().lower()
        
        custom_name = None
        if len(context.args) > 1:
            custom_name = ' '.join(context.args[1:]).strip()
            if len(custom_name) > 50:
                custom_name = custom_name[:50]
        
        if not wallet_address.startswith('0x') or len(wallet_address) != 42:
            await update.message.reply_text(
                "❌ Invalid wallet address format.\n\n"
                "Wallet addresses should start with '0x' and be 42 characters long.",
                reply_markup=get_back_button()
            )
            return
        
        user_id = update.effective_user.id
        
        tracked_wallets = await self.db.get_tracked_wallets(user_id)
        existing = next((w for w in tracked_wallets if w['address'].lower() == wallet_address.lower()), None)
        
        if existing:
            if custom_name:
                await self.db.update_wallet_custom_name(user_id, wallet_address, custom_name)
                await update.message.reply_text(
                    f"✅ Updated wallet name to **{custom_name}**\n\n"
                    f"`{format_wallet_address(wallet_address)}`",
                    parse_mode='Markdown',
                    reply_markup=get_back_button()
                )
                return
            else:
                display_name = existing.get('display_name', format_wallet_address(wallet_address))
                await update.message.reply_text(
                    f"ℹ️ You're already tracking **{display_name}**\n\n"
                    f"`{format_wallet_address(wallet_address)}`\n\n"
                    f"💡 Tip: Use `/track {wallet_address} <name>` to give it a custom name!",
                    parse_mode='Markdown',
                    reply_markup=get_back_button()
                )
                return
        
        is_pro = await self.db.is_pro(user_id)
        custom_count = len([w for w in tracked_wallets if w.get('wallet_type') == WalletType.CUSTOM.value])
        
        if not is_pro and custom_count >= TierLimits.FREE_MAX_CUSTOM_WALLETS:
            keyboard = [
                [InlineKeyboardButton("💎 Upgrade to PRO", callback_data="menu_upgrade")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]
            await update.message.reply_text(
                f"❌ You've reached the free tier limit of {TierLimits.FREE_MAX_CUSTOM_WALLETS} custom wallets.\n\n"
                f"Upgrade to PRO for unlimited wallet tracking!",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        success = await self.db.add_tracked_wallet(
            user_id=user_id,
            wallet_address=wallet_address,
            custom_name=custom_name,
            wallet_type=WalletType.CUSTOM.value
        )
        
        if not success:
            await update.message.reply_text(
                "❌ Failed to save wallet tracking. Please try again.",
                reply_markup=get_back_button()
            )
            return
        
        await update.message.reply_text("⏳ Fetching wallet information...")
        stats = await self.polymarket.get_wallet_stats(wallet_address)
        
        wallet_name = custom_name or stats.get('name') or format_wallet_address(wallet_address)
        
        roi = stats.get('roi_all_time', 0)
        roi_emoji = "🟢" if roi >= 0 else "🔴"
        
        pnl = stats.get('pnl_all_time', 0)
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        
        win_rate = stats.get('win_rate')
        win_rate_str = f"{win_rate:.1f}%" if win_rate is not None else "N/A"
        
        name_note = f"\n📝 Custom name: **{custom_name}**" if custom_name else ""
        
        keyboard = [
            [InlineKeyboardButton("📊 View Tracked Wallets", callback_data="menu_wallets")],
            [InlineKeyboardButton("➕ Track Another", callback_data="menu_track")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
        ]
        
        response = f"""
✅ **Now tracking:** `{wallet_address}`{name_note}

📊 **{wallet_name}**

**Performance:**
• ROI: {roi_emoji} {roi:+.2f}%
• PnL: {pnl_emoji} ${pnl:+,.2f}
• Win Rate: {win_rate_str}

**Activity:**
• 7-Day Volume: ${stats.get('volume_7d', 0):,.2f}
• Total Trades: {stats.get('total_trades', 0):,}

🔔 You'll receive notifications when this wallet makes trades!
"""
        
        await update.message.reply_text(
            response, 
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.info(f"User {user_id} started tracking wallet {wallet_address}")
        
    async def _track_leaderboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Track top 5 traders from a specific leaderboard."""
        user_id = update.effective_user.id
        
        time_period, period_display, category, category_display = self._parse_leaderboard_args(context.args or [])
        
        await update.message.reply_text(
            f"⏳ Fetching top 5 {period_display.lower()} traders in {category_display}..."
        )
        
        traders = await self.polymarket.get_leaderboard(
            limit=LEADERBOARD_TRACK_LIMIT,
            time_period=time_period,
            category=category,
            order_by='PNL'
        )
        
        if not traders:
            await update.message.reply_text(
                "❌ Unable to fetch leaderboard data. Please try again later.",
                reply_markup=get_back_button()
            )
            return

        # ── Tier quota check ──────────────────────────────────────────
        is_pro = await self.db.is_pro(user_id)
        if not is_pro:
            counts = await self.db.get_user_wallet_counts(user_id)
            current_leaderboard = counts['leaderboard']
            max_leaderboard = TierLimits.FREE_MAX_TAGWISE_TRADERS
            if current_leaderboard >= max_leaderboard:
                keyboard = [
                    [InlineKeyboardButton("💎 Upgrade to PRO", callback_data="menu_upgrade")],
                    [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
                ]
                await update.message.reply_text(
                    f"❌ You've reached the free tier limit of {max_leaderboard} leaderboard traders.\n\n"
                    f"🔥 Upgrade to PRO to track all top traders!",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            # Trim the list so FREE users only fill their remaining slots
            available = max_leaderboard - current_leaderboard
            if len(traders) > available:
                traders = traders[:available]

        await self.db.update_leaderboard_wallets(traders)
        
        # Get current wallet count BEFORE adding
        currently_tracked = await self.db.get_tracked_wallets(user_id)
        initial_count = len(currently_tracked)
        
        logger.info(f"User {user_id} currently tracking {initial_count} wallets total")
        
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
                logger.debug(f"  ✅ Newly added: {address[:10]}...")
            else:
                logger.debug(f"  📝 Already tracked: {address[:10]}...")
        
        await self.db.set_leaderboard_subscription(user_id, True)
        
        # Calculate total: previous count + newly added
        total_tracked = initial_count + new_count
        
        # Build trader list with better name formatting
        trader_list = []
        for i, trader in enumerate(traders, 1):
            # Get name with better fallback logic
            display_name = trader.get('display_name', '').strip()
            username = trader.get('username', '').strip()
            address = trader.get('address', '')
            
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
        
        response = f"""
        ✅ **Now Tracking Top {len(traders)} Traders!**

        **Leaderboard:** {category_display} | {period_display}

        **Traders:**
        {chr(10).join(trader_list)}

        📊 **Newly added:** {new_count}
        📝 **Total tracked:** {total_tracked}

        🔔 You'll receive notifications when these traders make moves!
        """
        
        await update.message.reply_text(
            response, 
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.info(f"✅ User {user_id} tracking result: {new_count} new, {total_tracked} total")
    

    async def name_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /name command - Set a custom name for a tracked wallet."""
        try:
            if len(context.args) < 2:
                await update.message.reply_text(
                    "❌ Please provide a wallet address and name.\n\n"
                    "Usage: `/name <wallet_address> <custom_name>`\n\n"
                    "Example: `/name 0x1234...5678 TopTrader`",
                    parse_mode='Markdown',
                    reply_markup=get_back_button()
                )
                return
            
            wallet_address = context.args[0].strip().lower()
            custom_name = ' '.join(context.args[1:]).strip()
            
            if len(custom_name) > 50:
                custom_name = custom_name[:50]
            
            user_id = update.effective_user.id
            
            tracked_wallets = await self.db.get_tracked_wallets(user_id)
            if wallet_address not in [w['address'].lower() for w in tracked_wallets]:
                await update.message.reply_text(
                    f"❌ You're not tracking this wallet.\n\n"
                    f"Use `/track {format_wallet_address(wallet_address)}` first.",
                    parse_mode='Markdown',
                    reply_markup=get_back_button()
                )
                return
            
            success = await self.db.update_wallet_custom_name(user_id, wallet_address, custom_name)
            
            if success:
                await update.message.reply_text(
                    f"✅ Wallet renamed to **{custom_name}**\n\n"
                    f"`{wallet_address}`",
                    parse_mode='Markdown',
                    reply_markup=get_back_button()
                )
            else:
                await update.message.reply_text(
                    "❌ Failed to update wallet name. Please try again.",
                    reply_markup=get_back_button()
                )
            
        except Exception as e:
            logger.error(f"Error in name_wallet: {e}", exc_info=True)
            await update.message.reply_text("❌ An error occurred. Please try again.")
    
    async def list_wallets(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /wallets command - List all tracked wallets."""
        try:
            user_id = update.effective_user.id
            message, keyboard = await self.bot.displays.render_wallets_view(user_id)
            
            await update.message.reply_text(
                message, 
                parse_mode='Markdown',
                reply_markup=keyboard
            )
            
        except Exception as e:
            logger.error(f"Error in list_wallets: {e}", exc_info=True)
            await update.message.reply_text("❌ An error occurred. Please try again.")

    async def untrack_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /untrack command - Untrack wallet(s)."""
        try:
            if not context.args:
                keyboard = [
                    [InlineKeyboardButton("🗑️ Untrack Leaderboard", callback_data="untrack_leaderboard")],
                    [InlineKeyboardButton("🗑️ Untrack All", callback_data="untrack_all")],
                    [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
                ]
                await update.message.reply_text(
                    "🗑️ **Untrack Wallets**\n\n"
                    "`/untrack <wallet_address>` - Stop tracking a specific wallet\n\n"
                    "Or use the buttons below:",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
            user_id = update.effective_user.id
            first_arg = context.args[0].strip().lower()
            
            if first_arg == 'leaderboard':
                removed_count = await self.db.remove_all_tagwise_wallets(user_id)
                await self.db.set_leaderboard_subscription(user_id, False)
                
                await update.message.reply_text(
                    f"✅ Stopped tracking **{removed_count}** leaderboard trader(s).\n\n"
                    f"Your custom wallets are still being tracked.",
                    parse_mode='Markdown',
                    reply_markup=get_back_button()
                )
                return
            
            if first_arg == 'all':
                removed_count = await self.db.remove_all_wallets(user_id)
                await self.db.set_leaderboard_subscription(user_id, False)
                
                await update.message.reply_text(
                    f"✅ Stopped tracking **{removed_count}** wallet(s).",
                    parse_mode='Markdown',
                    reply_markup=get_back_button()
                )
                return
            
            wallet_address = first_arg
            display_name = await self.db.get_wallet_display_name(user_id, wallet_address)
            
            success = await self.db.remove_tracked_wallet(user_id, wallet_address)
            
            if success:
                await update.message.reply_text(
                    f"✅ Stopped tracking **{display_name}**",
                    parse_mode='Markdown',
                    reply_markup=get_back_button()
                )
            else:
                await update.message.reply_text(
                    f"❌ You're not tracking this wallet.",
                    reply_markup=get_back_button()
                )
                
        except Exception as e:
            logger.error(f"Error in untrack_wallet: {e}", exc_info=True)
            await update.message.reply_text("❌ An error occurred. Please try again.")
    
    async def wallet_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command."""
        try:
            if not context.args:
                await update.message.reply_text(
                    "❌ Please provide a wallet address.\n\n"
                    "Usage: `/stats <wallet_address>`\n\n"
                    "💡 Or use the **Analyze Wallet** button from the main menu!",
                    parse_mode='Markdown',
                    reply_markup=get_back_button()
                )
                return
            
            wallet_address = context.args[0].strip().lower()
            
            if not wallet_address.startswith('0x') or len(wallet_address) != 42:
                await update.message.reply_text(
                    "❌ Invalid wallet address format.",
                    reply_markup=get_back_button()
                )
                return
            
            await update.message.reply_text("⏳ Fetching wallet statistics...")
            
            user_id = update.effective_user.id
            custom_name = None
            
            tracked = await self.db.get_tracked_wallets(user_id)
            is_tracking = False
            for w in tracked:
                if w['address'].lower() == wallet_address:
                    custom_name = w.get('custom_name')
                    is_tracking = True
                    break
            
            stats = await self.polymarket.get_wallet_stats(wallet_address)
            response = format_wallet_stats(wallet_address, stats, custom_name)
            
            keyboard = []
            if not is_tracking:
                keyboard.append([
                    InlineKeyboardButton("📌 Track This Wallet", callback_data=f"trackwallet_{wallet_address}")
                ])
            keyboard.extend([
                [InlineKeyboardButton("🔍 Analyze Another", callback_data="menu_analyze")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ])
            
            await update.message.reply_text(
                response, 
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        except Exception as e:
            logger.error(f"Error in wallet_stats: {e}", exc_info=True)
            await update.message.reply_text("❌ An error occurred. Please try again.")


    async def performance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot performance metrics (admin only)"""
        user_id = update.effective_user.id
        
        if user_id not in Config.ADMIN_USER_IDS:
            await update.message.reply_text("⛔ Admin only")
            return
        
        try:
            # Get metrics
            metrics = self.bot.scheduled_tasks.metrics
            queue_stats = self.bot.notification_queue.get_stats()
            
            # Get total wallets
            all_wallets = await self.bot.db.get_all_tracked_wallets()
            
            message = f"""
    📊 **Bot Performance Dashboard**

    **Last Monitor Cycle:**
    • Duration: {metrics.duration():.2f}s
    • Wallets checked: {metrics.wallets_checked}
    • Trades found: {metrics.trades_found}
    • Notifications sent: {metrics.notifications_sent}
    • Errors: {metrics.errors}
    {f"• Avg time/wallet: {metrics.duration()/metrics.wallets_checked:.3f}s" if metrics.wallets_checked > 0 else ""}

    **Notification Queue:**
    • Total sent: {queue_stats.get('sent', 0)}
    • Failed: {queue_stats.get('failed', 0)}
    • Pending: {queue_stats.get('pending', 0)}
    • Rate limit: {Config.TELEGRAM_RATE_LIMIT_PER_SECOND}/s

    **Monitoring:**
    • Total wallets: {len(all_wallets)}
    • Concurrent checks: {Config.MAX_CONCURRENT_WALLET_CHECKS}
    • Check interval: {Config.TRADE_CHECK_INTERVAL}s
    """
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error in performance command: {e}")
            await update.message.reply_text("Error fetching performance data")


    async def claim_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /claim command — redeem all resolved winning positions."""
        user_id = update.effective_user.id

        msg = await update.message.reply_text("🔍 Checking for claimable positions...")

        result = await self.wallet_manager.claim_winnings(user_id)

        if not result['success']:
            await msg.edit_text(f"❌ {result['error']}")
            return

        claimed = result.get('claimed', [])
        failed = result.get('failed', [])
        total = result.get('total_claimed', 0.0)

        if not claimed and not failed:
            await msg.edit_text("📭 No redeemable positions found.\n\nOnly resolved winning markets can be claimed.")
            return

        # Build response
        lines = ["✅ *Claim Results*\n"]

        if claimed:
            lines.append("*Claimed:*")
            for p in claimed:
                lines.append(f"  • {p['market'][:40]} — ${p['amount']:.2f}")
            lines.append(f"\n💰 *Total: ${total:.2f} USDC*")

        if failed:
            lines.append(f"\n⚠️ *Failed ({len(failed)}):*")
            for name in failed:
                lines.append(f"  • {name[:40]}")

        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
