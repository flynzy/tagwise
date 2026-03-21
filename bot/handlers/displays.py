"""Shared display/view logic for bot responses."""

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import Config, TierLimits
from bot.services.database import WalletType
from bot.handlers.formatters import escape_markdown
from bot.nowpayments import nowpayments_service

logger = logging.getLogger(__name__)


class DisplayViews:
    """Centralized display logic shared by commands and menus."""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.polymarket = bot.polymarket
        self.wallet_manager = bot.wallet_manager
        self.copy_manager = bot.copy_manager
    
    async def render_account_view(self, user_id: int, username: str = None):
        """Render account information view."""
        if username:
            username = escape_markdown(username)
        else:
            username = "N/A"
        
        sub_info = await self.db.get_subscription_info(user_id)
        is_pro = await self.db.is_pro(user_id)
        
        tracked_wallets = await self.db.get_tracked_wallets(user_id)
        custom_count = len([w for w in tracked_wallets if w.get('wallet_type') == WalletType.CUSTOM.value])
        tagwise_count = len([w for w in tracked_wallets if w.get('wallet_type') == WalletType.TAGWISE.value])
        
        if is_pro:
            tier_badge = "💎 PRO"
            days_left = sub_info.get('days_remaining', 0)
            expires_at = sub_info.get('expires_at')
            expires = expires_at.strftime('%Y-%m-%d') if expires_at else 'N/A'
            tier_info = f"**Expires:** {expires}\n**Days Remaining:** {days_left}"
            limits_info = (
                f"• Custom Wallets: {custom_count} / ∞\n"
                f"• Leaderboard Traders: {tagwise_count} / ∞\n"
                f"• Confidence Scores: ✅\n"
                f"• Multi-buy Alerts: ✅"
            )
        else:
            tier_badge = "🆓 Free"
            tier_info = "Upgrade to unlock all features!"
            max_custom = TierLimits.FREE_MAX_CUSTOM_WALLETS
            max_tagwise = TierLimits.FREE_MAX_TAGWISE_TRADERS
            limits_info = (
                f"• Custom Wallets: {custom_count} / {max_custom}\n"
                f"• Leaderboard Traders: {tagwise_count} / {max_tagwise}\n"
                f"• Confidence Scores: ❌\n"
                f"• Multi-buy Alerts: ❌"
            )
        
        # ✅ NEW: Referral stats section
        referral_stats = await self.db.get_referral_stats(user_id)
        ref_code = referral_stats.get('referral_code')
        total_referrals = referral_stats.get('total_referrals', 0)
        converted = referral_stats.get('converted', 0)
        total_days_earned = referral_stats.get('total_days_earned', 0)
        
        referral_section = (
            f"\n🎁 **Referrals:**\n"
            f"• Your Code: `{ref_code}`\n"
            f"• Invited: {total_referrals} user{'s' if total_referrals != 1 else ''}\n"
            f"• Converted to PRO: {converted}\n"
            f"• PRO Days Earned: {total_days_earned}"
        )
        
        message = f"""
👤 **Your Account**

**User:** @{username}
**Tier:** {tier_badge}
{tier_info}

📊 **Usage:**
{limits_info}
{referral_section}
"""
        
        keyboard = []
        keyboard.append([InlineKeyboardButton("🎁 Referral & Rewards", callback_data="menu_referral")])
        if not is_pro:
            keyboard.append([InlineKeyboardButton("💎 Upgrade to PRO", callback_data="menu_upgrade")])
        keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")])
        
        return message, InlineKeyboardMarkup(keyboard)
    
    async def render_referral_view(self, user_id: int):
        """Render the dedicated referral & rewards page."""
        referral_stats = await self.db.get_referral_stats(user_id)
        ref_code = referral_stats.get('referral_code')
        total_referrals = referral_stats.get('total_referrals', 0)
        converted = referral_stats.get('converted', 0)
        total_days_earned = referral_stats.get('total_days_earned', 0)
        
        bot_username = Config.BOT_USERNAME if hasattr(Config, 'BOT_USERNAME') else "tagwise_bot"
        referral_link = f"https://t.me/{bot_username}?start=ref_{ref_code}"
        
        message = f"""
🎁 **Referral & Rewards**

Share Tagwise with friends and earn free PRO days!

**How It Works:**
1️⃣ Share your unique referral link
2️⃣ Your friend gets a **3-day free PRO trial**
3️⃣ When they subscribe to PRO, you earn **7 days of free PRO**

**Your Referral Link:**
`{referral_link}`

**Your Code:** `{ref_code}`

📊 **Your Stats:**
• Friends Invited: {total_referrals}
• Converted to PRO: {converted}
• Total PRO Days Earned: {total_days_earned}
"""
        
        keyboard = [
            [InlineKeyboardButton("📋 Copy Referral Link", callback_data="referral_share")],
            [InlineKeyboardButton("👤 Account", callback_data="menu_account")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
        ]
        
        return message, InlineKeyboardMarkup(keyboard)
    
    async def render_upgrade_view(self, user_id: int):
        """Render upgrade/payment view."""
        is_pro = await self.db.is_pro(user_id)
        
        if is_pro:
            sub_info = await self.db.get_subscription_info(user_id)
            days_left = sub_info.get('days_remaining', 0)
            
            message = (
                f"✅ **You're already PRO!**\n\n"
                f"Your subscription is active for **{days_left}** more days."
            )
            keyboard = [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")]]
            return message, InlineKeyboardMarkup(keyboard)
        
        # Generate payment links via Nowpayments (async)
        monthly_payment = await nowpayments_service.create_payment(user_id, "monthly")
        annual_payment = await nowpayments_service.create_payment(user_id, "annual")
        
        keyboard = []
        
        if annual_payment:
            keyboard.append([
                InlineKeyboardButton(
                    f"🔥 Annual ${TierLimits.PRO_PRICE_ANNUAL:.0f}/yr (Save 43%!)",
                    url=annual_payment["payment_url"]
                )
            ])
        
        if monthly_payment:
            keyboard.append([
                InlineKeyboardButton(
                    f"📅 Monthly ${TierLimits.PRO_PRICE_MONTHLY:.0f}/mo",
                    url=monthly_payment["payment_url"]
                )
            ])
        
        keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")])
        
        if len(keyboard) == 1:  # Only back button
            message = "❌ Unable to generate payment links. Please try again later."
            return message, InlineKeyboardMarkup(keyboard)
        
        message = f"""
🚀 **Upgrade to Tagwise PRO**

**What You Get:**
• 🎯 Confidence scores on every alert
• 📊 Unlimited wallet tracking  
• ⭐ Track ALL top traders
• 🔍 Advanced leaderboard filters
• 🔊 Multi-buy alerts

**Pricing:**
• Monthly: ${TierLimits.PRO_PRICE_MONTHLY:.0f}/month
• Annual: ${TierLimits.PRO_PRICE_ANNUAL:.0f}/year (Best Value!)

👇 **Click to pay with crypto:**
"""
        
        return message, InlineKeyboardMarkup(keyboard)
    
    async def render_wallets_view(self, user_id: int):
        """Render tracked wallets view."""
        wallets = await self.db.get_tracked_wallets(user_id)
        
        if not wallets:
            keyboard = [
                [InlineKeyboardButton("➕ Track a Wallet", callback_data="menu_track")],
                [InlineKeyboardButton("🏆 Track from Leaderboard", callback_data="menu_toptraders")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
            ]
            message = (
                "📭 **No Tracked Wallets**\n\n"
                "You're not tracking any wallets yet.\n\n"
                "Choose an option to start tracking:"
            )
            return message, InlineKeyboardMarkup(keyboard)
        
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
                response += f"• **{name}**\n  `{address}`\n\n"
            
            if len(tagwise_wallets) > 8:
                response += f"\n_...and {len(tagwise_wallets) - 8} more_\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Track More", callback_data="menu_track")],
            [
                InlineKeyboardButton("🗑️ Untrack Leaderboard", callback_data="untrack_leaderboard"),
                InlineKeyboardButton("🗑️ Untrack All", callback_data="untrack_all"),
            ],
            [InlineKeyboardButton("🔄 Refresh", callback_data="menu_wallets")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
        ]
        
        return response, InlineKeyboardMarkup(keyboard)
