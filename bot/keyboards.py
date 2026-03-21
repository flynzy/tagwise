"""Keyboard builders for the Telegram bot."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def get_back_button(callback_data: str = "menu_main") -> InlineKeyboardMarkup:
    """Get a back to menu button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data=callback_data)]
    ])


def get_main_menu_keyboard(is_pro: bool = False) -> InlineKeyboardMarkup:
    """Get the main menu keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🏆 Top Traders", callback_data="menu_toptraders"),
            InlineKeyboardButton("📊 Wallet Tracker", callback_data="menu_wallet_tracker"),
        ],
        [
            InlineKeyboardButton("🔍 Analyze Wallet", callback_data="menu_analyze"),
            InlineKeyboardButton("💳  Trading Wallet", callback_data="menu_trading_wallet"),
        ],
        [
            InlineKeyboardButton("🤖 Copy Trading", callback_data="menu_copytrade"),
            InlineKeyboardButton("👤 Account", callback_data="menu_account"),
        ],
        [
            InlineKeyboardButton("🎁 Referral & Rewards", callback_data="menu_referral"),
            InlineKeyboardButton("❓ Help", callback_data="menu_help"),
        ],
    ]
    
    if not is_pro:
        keyboard.append([
            InlineKeyboardButton("💎 Upgrade to PRO", callback_data="menu_upgrade")
        ])
    
    return InlineKeyboardMarkup(keyboard)


def get_wallet_tracker_keyboard() -> InlineKeyboardMarkup:
    """Get the wallet tracker submenu keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 My Wallets", callback_data="menu_wallets")],
        [InlineKeyboardButton("➕ Track Custom Wallet", callback_data="menu_track")],
        [InlineKeyboardButton("🏆 Track from Leaderboard", callback_data="menu_toptraders")],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
    ])


def get_toptraders_category_keyboard() -> InlineKeyboardMarkup:
    """Get the top traders category selection keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Overall", callback_data="topcat_OVERALL")],
        [InlineKeyboardButton("🏛️ Politics", callback_data="topcat_POLITICS")],
        [InlineKeyboardButton("⚽ Sports", callback_data="topcat_SPORTS")],
        [InlineKeyboardButton("₿ Crypto", callback_data="topcat_CRYPTO")],
        [InlineKeyboardButton("🎭 Culture", callback_data="topcat_CULTURE")],
        [InlineKeyboardButton("💻 Tech", callback_data="topcat_TECH")],
        [InlineKeyboardButton("💰 Finance", callback_data="topcat_FINANCE")],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")],
    ])


def get_time_period_keyboard(category: str) -> InlineKeyboardMarkup:
    """Get the time period selection keyboard for a category."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Daily", callback_data=f"top_{category}_DAY"),
            InlineKeyboardButton("📆 Weekly", callback_data=f"top_{category}_WEEK"),
        ],
        [
            InlineKeyboardButton("🗓️ Monthly", callback_data=f"top_{category}_MONTH"),
            InlineKeyboardButton("🏆 All-Time", callback_data=f"top_{category}_ALL"),
        ],
        [InlineKeyboardButton("⬅️ Back to Categories", callback_data="menu_toptraders")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
    ])


def get_leaderboard_results_keyboard(category: str, time_period: str) -> InlineKeyboardMarkup:
    """Get the keyboard for leaderboard results."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📌 Track Top 5", callback_data=f"trackld_{category}_{time_period}")],
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"top_{category}_{time_period}")],
        [InlineKeyboardButton("⬅️ Change Time Period", callback_data=f"topcat_{category}")],
        [InlineKeyboardButton("⬅️ Change Category", callback_data="menu_toptraders")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
    ])
