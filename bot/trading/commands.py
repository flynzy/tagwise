# bot/trading/commands.py
"""
Telegram command handlers for gasless wallet and copy trading.
"""

import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters
)

from bot.trading.wallet_manager import WalletManager
from bot.trading.copy_trader import CopyTradeManager, CopyTradeSettings, SettingsLimits

logger = logging.getLogger(__name__)

# Conversation states
AWAITING_PRIVATE_KEY = 1
AWAITING_WITHDRAW_ADDRESS = 2
AWAITING_WITHDRAW_AMOUNT = 3
AWAITING_CUSTOM_SETTING = 4


class TradingCommands:
    """Handler class for trading commands with gasless operations"""
    
    def __init__(self, db, wallet_manager: WalletManager, copy_manager: CopyTradeManager, bot=None):
        self.db = db
        self.wallet_manager = wallet_manager
        self.copy_manager = copy_manager
        self.bot = bot
        self._pending_imports = {}
        self._pending_withdraws = {}
        self._pending_settings = {}  # Track which setting user is editing
        self._pending_wallet_name = {}  # Track users naming a new wallet
    
    # ==================== WALLET COMMANDS ====================
    
    async def wallet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # ✅ ADD NULL CHECK
        if self.bot and hasattr(self.bot, 'menu_handlers'):
            await self.bot.menu_handlers.show_trading_wallet(update, user_id, context)
        else:
            # Fallback: show a basic message
            keyboard = [
                [InlineKeyboardButton("💳 View Wallet", callback_data="menu_trading_wallet")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
            ]
            await update.message.reply_text(
                "💳 **Trading Wallet**\n\nUse the buttons below to manage your wallet:",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    async def wallet_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle wallet-related callbacks"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        action = query.data.replace("wallet_", "")

        if action == "portfolio_pick":
            wallets = await self.wallet_manager.get_wallets(user_id)
            if not wallets:
                await self.bot.menu_handlers.show_trading_wallet(query, user_id)
                return
            if len(wallets) == 1:
                await query.edit_message_text("⏳ Loading portfolio...")
                await self.bot.menu_handlers.show_portfolio(query, user_id, wallet_db_id=wallets[0]['id'])
                return
            keyboard = []
            for w in wallets:
                wname = w.get('wallet_name') or f"Wallet {w['wallet_index']}"
                active_marker = " ✅" if w.get('is_active') else ""
                keyboard.append([InlineKeyboardButton(
                    f"📊 {wname}{active_marker}", callback_data=f"wallet_portfolio_{w['id']}"
                )])
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_trading_wallet")])
            await query.edit_message_text(
                "📊 *Portfolio*\n\nChoose which wallet to view:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        elif action.startswith("portfolio_"):
            wallet_db_id = int(action.replace("portfolio_", ""))
            await query.edit_message_text("⏳ Loading portfolio...")
            await self.bot.menu_handlers.show_portfolio(query, user_id, wallet_db_id=wallet_db_id)

        elif action == "portfolio":
            await query.edit_message_text("⏳ Loading portfolio...")
            await self.bot.menu_handlers.show_portfolio(query, user_id)

        elif action.startswith("positions_"):
            wallet_db_id = int(action.replace("positions_", ""))
            await query.edit_message_text("⏳ Loading positions...")
            await self.bot.menu_handlers.show_open_positions(query, user_id, wallet_db_id)

        elif action.startswith("wonmarkets_"):
            wallet_db_id = int(action.replace("wonmarkets_", ""))
            await query.edit_message_text("⏳ Loading won markets...")
            await self.bot.menu_handlers.show_won_markets(query, user_id, wallet_db_id)

        elif action.startswith("lostmarkets_"):
            wallet_db_id = int(action.replace("lostmarkets_", ""))
            await query.edit_message_text("⏳ Loading lost markets...")
            await self.bot.menu_handlers.show_lost_markets(query, user_id, wallet_db_id)

        elif action == "add":
            await query.edit_message_text(
                "➕ *Add New Wallet*\n\n"
                "Send a name for your new wallet:\n"
                "_(e.g. 'Trading', 'Savings')_\n\n"
                "_Or click Cancel below._",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data="menu_trading_wallet")
                ]])
            )
            self._pending_wallet_name[user_id] = True

        elif action == "setactive":
            wallets = await self.wallet_manager.get_wallets(user_id)
            if len(wallets) <= 1:
                await self.bot.menu_handlers.show_trading_wallet(query, user_id)
                return
            keyboard = []
            for w in wallets:
                wname = w.get('wallet_name') or f"Wallet {w['wallet_index']}"
                active_marker = " ✅" if w.get('is_active') else ""
                keyboard.append([InlineKeyboardButton(
                    f"{wname}{active_marker}", callback_data=f"wallet_setactive_{w['id']}"
                )])
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_trading_wallet")])
            await query.edit_message_text(
                "🔑 *Set Active Wallet*\n\nChoose which wallet to use for trading:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        elif action.startswith("setactive_"):
            wallet_db_id = int(action.replace("setactive_", ""))
            ok = await self.wallet_manager.set_active_wallet(user_id, wallet_db_id)
            if ok:
                await query.answer("✅ Active wallet updated!", show_alert=False)
            await self.bot.menu_handlers.show_trading_wallet(query, user_id)

        elif action == "withdraw_pick":
            wallets = await self.wallet_manager.get_wallets(user_id)
            if len(wallets) == 1:
                # Only one wallet — go straight to withdraw
                self._pending_withdraws[user_id] = {'wallet': wallets[0]}
                await self.start_withdraw(query, user_id, wallet=wallets[0])
                return
            keyboard = []
            for w in wallets:
                wname = w.get('wallet_name') or f"Wallet {w['wallet_index']}"
                keyboard.append([InlineKeyboardButton(
                    f"📤 {wname}", callback_data=f"wallet_withdraw_sel_{w['id']}"
                )])
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_trading_wallet")])
            await query.edit_message_text(
                "💸 *Withdraw USDC*\n\nSelect the wallet to withdraw from:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        elif action.startswith("withdraw_sel_"):
            wallet_db_id = int(action.replace("withdraw_sel_", ""))
            wallet = await self.wallet_manager.get_wallet_by_id(user_id, wallet_db_id)
            if not wallet:
                await query.answer("Wallet not found.", show_alert=True)
                return
            await self.start_withdraw(query, user_id, wallet=wallet)

        elif action == "delete_pick":
            wallets = await self.wallet_manager.get_wallets(user_id)
            keyboard = []
            for w in wallets:
                wname = w.get('wallet_name') or f"Wallet {w['wallet_index']}"
                keyboard.append([InlineKeyboardButton(
                    f"🗑️ {wname}", callback_data=f"wallet_delete_{w['id']}"
                )])
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_trading_wallet")])
            await query.edit_message_text(
                "🗑️ *Delete Wallet*\n\nChoose which wallet to delete:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        elif action.startswith("delete_") and not action.startswith("delete_pick") and not action.startswith("delete_sel"):
            try:
                wallet_db_id = int(action.replace("delete_", ""))
                wallet = await self.wallet_manager.get_wallet_by_id(user_id, wallet_db_id)
                if not wallet:
                    await query.answer("Wallet not found.", show_alert=True)
                    return
                wname = wallet.get('wallet_name') or f"Wallet {wallet['wallet_index']}"
                keyboard = [
                    [InlineKeyboardButton("⚠️ Yes, Delete", callback_data=f"wallet_confirmdelete_{wallet_db_id}")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="menu_trading_wallet")],
                ]
                await query.edit_message_text(
                    f"⚠️ *Delete {wname}?*\n\n"
                    "This removes the wallet from Tagwise.\n"
                    "Your funds remain safe on the blockchain.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except ValueError:
                pass

        elif action.startswith("confirmdelete_"):
            try:
                wallet_db_id = int(action.replace("confirmdelete_", ""))
                success = await self.wallet_manager.delete_wallet_by_id(user_id, wallet_db_id)
                keyboard = [[InlineKeyboardButton("🔙 Back to Wallets", callback_data="menu_trading_wallet")]]
                if success:
                    await query.edit_message_text(
                        "🗑️ *Wallet Deleted*\n\n"
                        "The wallet has been removed from Tagwise.\n"
                        "Your funds remain safe on-chain.",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    await query.edit_message_text("❌ Failed to delete wallet.", reply_markup=InlineKeyboardMarkup(keyboard))
            except ValueError:
                pass

        elif action in ("create", "import"):
            # Wallet provisioning is now automatic — just show the wallet page
            await self.bot.menu_handlers.show_trading_wallet(query, user_id)

        elif action == "setup":
            # Setup is now automatic — if they somehow hit this, just re-trigger it
            await query.edit_message_text(
                "⏳ *Setting up your wallet...*\n\nThis usually takes 30–60 seconds.",
                parse_mode="Markdown"
            )
            asyncio.create_task(self.bot._auto_provision_wallet(user_id))
            # Show wallet page immediately so they see the loading state
            await self.bot.menu_handlers.show_trading_wallet(query, user_id)

        elif action in ("balance", "refresh"):
            await query.edit_message_text(
                "⏳ *Refreshing wallet and syncing balance...*\n\n_Please wait_",
                parse_mode="Markdown"
            )
            result = await self.wallet_manager._activate_trading(user_id)
            if not result['success']:
                logger.warning(f"Balance sync failed for user {user_id}: {result.get('error')}")
            await self.bot.menu_handlers.show_trading_wallet(query, user_id)

        elif action == "withdraw":
            result = await self.start_withdraw(query, user_id)
            if result == AWAITING_WITHDRAW_ADDRESS:
                return AWAITING_WITHDRAW_ADDRESS

        elif action == "claim":
            await self._handle_wallet_claim(query, user_id)
        
        elif action == "delete":
            keyboard = [
                [InlineKeyboardButton("⚠️ Yes, Delete", callback_data="wallet_confirmdelete")],
                [InlineKeyboardButton("❌ Cancel", callback_data="wallet_cancel")]
            ]
            await query.edit_message_text(
                "⚠️ **Delete Wallet?**\n\n"
                "This removes your wallet from Tagwise.\n"
                "Your funds remain safe on the blockchain.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif action == "confirmdelete":
            success = await self.wallet_manager.delete_wallet(user_id)

            keyboard = [
                [InlineKeyboardButton("Create New Wallet", callback_data="wallet_create")],
                [InlineKeyboardButton("Main Menu", callback_data="menu_main")],
            ]

            if success:
                await query.edit_message_text(
                    "🗑️ *Wallet Deleted*\n\n"
                    "Your trading wallet has been removed.\n"
                    "You can create a new wallet or import an existing one anytime.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                keyboard.insert(0, [InlineKeyboardButton("Back to Wallet", callback_data="menu_trading_wallet")])
                await query.edit_message_text(
                    "❌ Failed to delete wallet.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        elif action == "cancel":
            # ✅ ADD KEYBOARD INSTEAD OF JUST TEXT
            await query.answer("✅ Cancelled", show_alert=False)
            
            keyboard = [
                [InlineKeyboardButton("⬅️ Back to Wallet", callback_data="menu_trading_wallet")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
            ]
            
            await query.edit_message_text(
                "❌ **Cancelled**\n\n"
                "Wallet deletion cancelled.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)  # ✅ ADD THIS
            )


    async def receive_private_key(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Private key import is no longer supported — delete the message for safety."""
        user_id = update.effective_user.id

        try:
            await update.message.delete()
        except Exception:
            pass

        keyboard = [
            [InlineKeyboardButton("Create Wallet", callback_data="wallet_create")],
            [InlineKeyboardButton("Main Menu", callback_data="menu_main")],
        ]
        await context.bot.send_message(
            chat_id=user_id,
            text="Wallet import is no longer supported. "
                 "Create a new wallet instead — keys are secured by Privy.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return ConversationHandler.END

    async def _setup_safe(self, query, user_id: int):
        await query.edit_message_text("Setting up your Safe wallet...")
        result = await self.wallet_manager.setup_safe(user_id)

        if result["success"]:

            keyboard = [
                [InlineKeyboardButton("View Wallet", callback_data="menu_trading_wallet")],
                [InlineKeyboardButton("Set Up Copy Trading", callback_data="menu_copytrade")],
                [InlineKeyboardButton("Main Menu", callback_data="menu_main")],
            ]
            
            await query.edit_message_text(
                f"✅ **Setup Complete!**\n\n"
                f"**Polymarket Address:**\n`{result['safe_address']}`\n\n"
                f"Your wallet is ready for gasless trading!\n\n"
                f"Send USDC.e to your Polymarket address to start.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)  # ✅ ADD THIS
            )
        else:
            # ✅ ADD KEYBOARD FOR ERROR CASE
            keyboard = [
                [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_setup")],
                [InlineKeyboardButton("⬅️ Back to Wallet", callback_data="menu_trading_wallet")],
            ]
            await query.edit_message_text(
                f"❌ {result['error']}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
        
    async def start_withdraw(self, query, user_id: int, wallet: dict = None):
        """Start withdrawal flow. If wallet is provided, use its balance directly."""
        if wallet:
            bal = await self.wallet_manager.get_balances_for_wallet(wallet)
            available = bal.get('polymarket_usdc', 0.0)
        else:
            balances = await self.wallet_manager.get_balances(user_id)
            available = balances.get('polymarket_usdc', 0)

        if available < 0.01:
            keyboard = [
                [InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_trading_wallet")],
                [InlineKeyboardButton("Main Menu", callback_data="menu_main")],
            ]
            await query.edit_message_text(
                "💸 *No Funds to Withdraw*\n\nSend USDC to your Polymarket address first.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return None

        self._pending_withdraws[user_id] = {"available": available, "wallet": wallet}
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="wallet_cancel")]]
        await query.edit_message_text(
            f"📤 *Withdraw USDC*\n\n"
            f"Available: *${available:.2f}*\n\n"
            f"Send the destination address:\nOr click Cancel below",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return AWAITING_WITHDRAW_ADDRESS


    async def receive_withdraw_address(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive withdrawal address"""
        user_id = update.effective_user.id
        address = update.message.text.strip()
        
        if not address.startswith('0x') or len(address) != 42:
            # ✅ ADD KEYBOARD
            keyboard = [[InlineKeyboardButton("❌ Cancel Withdrawal", callback_data="wallet_cancel")]]
            
            await update.message.reply_text(
                "❌ **Invalid Address**\n\n"
                "Please send a valid Ethereum address (0x...):",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)  # ✅ ADD THIS
            )
            return AWAITING_WITHDRAW_ADDRESS
        
        if user_id not in self._pending_withdraws:
            # ✅ ADD KEYBOARD
            keyboard = [[InlineKeyboardButton("⬅️ Back to Wallet", callback_data="menu_trading_wallet")]]
            
            await update.message.reply_text(
                "⏳ **Session Expired**\n\n"
                "Use /wallet to try again.",
                reply_markup=InlineKeyboardMarkup(keyboard)  # ✅ ADD THIS
            )
            return ConversationHandler.END
        
        self._pending_withdraws[user_id]['to_address'] = address
        available = self._pending_withdraws[user_id]['available']
        
        # ✅ ADD CANCEL BUTTON
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="wallet_cancel")]]
        
        await update.message.reply_text(
            f"📤 **To:** `{address}`\n\n"
            f"Available: ${available:.2f}\n\n"
            f"Enter amount (or 'all'):\n\n"
            f"_Or click Cancel below_",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)  # ✅ ADD THIS
        )
        
        return AWAITING_WITHDRAW_AMOUNT

    async def receive_withdraw_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Execute withdrawal"""
        user_id = update.effective_user.id
        text = update.message.text.strip().lower()
        
        if user_id not in self._pending_withdraws:
            keyboard = [[InlineKeyboardButton("⬅️ Back to Wallet", callback_data="menu_trading_wallet")]]
            await update.message.reply_text(
                "⏳ **Session Expired**\n\n"
                "Use /wallet to try again.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return ConversationHandler.END
        
        pending = self._pending_withdraws[user_id]
        
        if text == 'all':
            amount = pending['available'] - 0.01
        else:
            try:
                amount = float(text.replace('$', '').replace(',', ''))
            except ValueError:
                # ✅ ADD KEYBOARD
                keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="wallet_cancel")]]
                
                await update.message.reply_text(
                    "❌ **Invalid Amount**\n\n"
                    "Please enter a valid number:",
                    reply_markup=InlineKeyboardMarkup(keyboard)  # ✅ ADD THIS
                )
                return AWAITING_WITHDRAW_AMOUNT
        
        if amount <= 0 or amount > pending['available']:
            # ✅ ADD KEYBOARD
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="wallet_cancel")]]
            
            await update.message.reply_text(
                f"❌ **Invalid Amount**\n\n"
                f"Amount must be between $0.01 and ${pending['available']:.2f}\n\n"
                f"Try again:",
                reply_markup=InlineKeyboardMarkup(keyboard)  # ✅ ADD THIS
            )
            return AWAITING_WITHDRAW_AMOUNT
        
        await update.message.reply_text("⏳ Processing...")
        
        result = await self.wallet_manager.withdraw_usdc(
            user_id, pending['to_address'], amount
        )
        
        del self._pending_withdraws[user_id]
        
        keyboard = [
            [InlineKeyboardButton("💰 Withdraw More", callback_data="wallet_withdraw")],
            [InlineKeyboardButton("⬅️ Back to Wallet", callback_data="menu_trading_wallet")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
        ]
        
        if result['success']:
            await update.message.reply_text(
                f"✅ **Withdrawal Successful!**\n\n"
                f"**Amount:** ${amount:.2f}\n"
                f"**To:** `{result['to_address']}`\n"
                f"**TX:** `{result.get('tx_hash', 'N/A')}`\n\n"
                f"⛽ No gas fees paid!",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                f"❌ **Withdrawal Failed**\n\n{result['error']}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        return ConversationHandler.END

    async def _handle_wallet_claim(self, query, user_id: int):
        """Handle claim winnings from inline button on trading wallet."""
        await query.edit_message_text("🔍 Checking for claimable positions...")

        result = await self.wallet_manager.claim_winnings(user_id)

        if not result.get("success"):
            keyboard = [
                [InlineKeyboardButton("⬅️ Back to Wallet", callback_data="menu_trading_wallet")],
            ]
            await query.edit_message_text(
                f"❌ {result.get('error', 'Failed to claim winnings.')}",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        claimed = result.get("claimed", [])
        failed = result.get("failed", [])
        total = result.get("total_claimed", 0.0)

        keyboard = [
            [InlineKeyboardButton("⬅️ Back to Wallet", callback_data="menu_trading_wallet")],
        ]

        if not claimed and not failed:
            await query.edit_message_text(
                "📭 No redeemable positions found.\n\n"
                "Only resolved winning markets can be claimed.",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        lines = ["✅ *Claim results*\n"]

        if claimed:
            lines.append("*Claimed:*")
            for p in claimed:
                title = (p["market"] or "Unknown")[:55]
                lines.append(f"• {title} — ${p['amount']:.2f}")
            lines.append(f"\n💰 *Total claimed:* ${total:.2f} USDC")

        if failed:
            lines.append(f"\n⚠️ *Failed ({len(failed)}):*")
            for name in failed:
                title = (name or "Unknown")[:55]
                lines.append(f"• {title}")

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )



    # ==================== COPY TRADE COMMANDS ====================
    
    async def copytrade_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /copytrade command"""
        user_id = update.effective_user.id
        await self._show_copytrade_main(update.message, user_id)
    

    async def _show_copytrade_main(self, message_or_query, user_id: int, edit: bool = False):
        """Show main copy trading page"""
        status = await self.wallet_manager.get_wallet_status(user_id)
        
        if not status.get('has_wallet'):
            keyboard = [[InlineKeyboardButton("💳 Set Up Wallet", callback_data="menu_trading_wallet")]]
            text = "❌ You need a wallet first.\n\nUse /wallet to set one up."
            
            if edit:
                await message_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await message_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        if not status.get('ready_to_trade'):
            keyboard = [[InlineKeyboardButton("🚀 Complete Setup", callback_data="wallet_setup")]]
            text = "⚠️ Complete your wallet setup first."
            
            if edit:
                await message_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await message_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        settings = await self.copy_manager.get_user_settings(user_id)
        
        status_emoji = "🟢" if settings.enabled else "🔴"
        status_text = "ON" if settings.enabled else "OFF"
        
        multi_buy_emoji = "🟢" if settings.multi_buy_only else "🔴"
        multi_buy_text = "ON" if settings.multi_buy_only else "OFF"
        
        # ✅ NEW: Add description for multi-buy only mode
        multi_buy_desc = ""
        if settings.multi_buy_only:
            multi_buy_desc = " - _Single wallet trades are muted & ignored_"
        
        message = f"""
🤖 **Copy Trading Dashboard** - _Automatically executes trades alongside alerts when enabled_

**Copy Trade:** {status_emoji} {status_text}
**Multi-Buy Mode:** {multi_buy_emoji} {multi_buy_text}{multi_buy_desc}

**📈 Buy Settings:**
• Amount: **{settings.get_buy_display()}**

**📉 Sell Settings:**
• Amount: **{settings.get_sell_display()}**

**🎯 Filters:**
• Price range: **${settings.min_price:.2f} - ${settings.max_price:.2f}**
• Min target trade: **${settings.min_target_trade_value:.0f}**

**Copy Options:**
• Buys: {'✅' if settings.copy_buys else '❌'}
• Sells: {'✅' if settings.copy_sells else '❌'}
    """
        
        keyboard = []
        
        # Enable/Disable button
        if settings.enabled:
            keyboard.append([InlineKeyboardButton("🟢 Disable Copy Trading", callback_data="copy_disable")])
        else:
            keyboard.append([InlineKeyboardButton("🔴 Enable Copy Trading", callback_data="copy_enable")])

        # Check if PRO for multi-buy
        is_pro = await self.db.is_pro(user_id)

        if is_pro:
            if settings.multi_buy_only:
                # ✅ UPDATED: Better button text
                keyboard.append([InlineKeyboardButton("🟢 Disable Multi-Buy Mode", callback_data="copy_multibuy_off")])
            else:
                # ✅ UPDATED: Better button text with description
                keyboard.append([InlineKeyboardButton("🔴 Enable Multi-Buy Mode", callback_data="copy_multibuy_on")])
        else:
            keyboard.append([InlineKeyboardButton("🔥 Multi-Buy Only Mode (PRO)", callback_data="menu_upgrade")])
        
        # Settings buttons
        keyboard.append([InlineKeyboardButton("⚙️ Configure Settings", callback_data="copy_settings")])
        
        # History button
        keyboard.append([InlineKeyboardButton("📈 View History", callback_data="copy_history")])
        
        # Back button
        keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_main")])
        
        if edit:
            await message_or_query.edit_message_text(
                message,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await message_or_query.reply_text(
                message,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    

    async def copytrade_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle copy trading callbacks"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        action = query.data.replace("copy_", "")

        logger.info(f"[copy_trade_callback] data={query.data!r} action={action!r}")
        
        # Main actions
        if action == "main":
            await self._show_copytrade_main(query, user_id, edit=True)
            return
        
        if action == "enable":
            await self._confirm_enable(query, user_id)
            return
        
        if action == "confirm_enable":
            await query.edit_message_text("⏳ Enabling copy trading...")
            settings = await self.copy_manager.get_user_settings(user_id)
            settings.enabled = True
            await self.copy_manager.save_user_settings(user_id, settings)
            self.copy_manager.clear_trader_cache(user_id)
            await self._show_copytrade_main(query, user_id, edit=True)
            return
        
        if action == "disable":
            await query.edit_message_text("⏳ Disabling copy trading...")
            settings = await self.copy_manager.get_user_settings(user_id)
            settings.enabled = False
            await self.copy_manager.save_user_settings(user_id, settings)
            self.copy_manager.clear_trader_cache(user_id)
            await self._show_copytrade_main(query, user_id, edit=True)
            return

        
        if action == "settings":
            await self._show_settings_menu(query, user_id)
            return
        
        if action == "history":
            await self._show_history(query, user_id)
            return
        
        # Buy/Sell settings submenus
        if action == "buy_settings":
            await self._show_buy_settings(query, user_id)
            return
        
        if action == "sell_settings":
            await self._show_sell_settings(query, user_id)
            return
        
        if action == "filter_settings":
            await self._show_filter_settings(query, user_id)
            return
        
        # Amount type toggles
        if action == "buy_type_fixed":
            await self.copy_manager.update_setting(user_id, 'buy_amount_type', 'fixed')
            await self._show_buy_settings(query, user_id)
            return
        
        if action == "buy_type_percentage":
            await self.copy_manager.update_setting(user_id, 'buy_amount_type', 'percentage')
            await self._show_buy_settings(query, user_id)
            return
        
        if action == "sell_type_fixed":
            await self.copy_manager.update_setting(user_id, 'sell_amount_type', 'fixed')
            await self._show_sell_settings(query, user_id)
            return
        
        if action == "sell_type_percentage":
            await self.copy_manager.update_setting(user_id, 'sell_amount_type', 'percentage_holdings')
            await self._show_sell_settings(query, user_id)
            return
        
        # Settings actions
        if action.startswith("set_"):
            await self._handle_setting_selection(query, user_id, action)
            return
        
        # Preset value selections
        if action.startswith("val_"):
            await self._handle_preset_value(query, user_id, action)
            return
        
        # Custom input
        if action.startswith("custom_"):
            setting_name = action.replace("custom_", "")
            return await self._start_custom_input(query, user_id, setting_name)
        
        # Toggle actions
        if action == "toggle_buys":
            settings = await self.copy_manager.get_user_settings(user_id)
            settings.copy_buys = not settings.copy_buys
            await self.copy_manager.save_user_settings(user_id, settings)
            await self._show_settings_menu(query, user_id)
            return
        
        if action == "toggle_sells":
            settings = await self.copy_manager.get_user_settings(user_id)
            settings.copy_sells = not settings.copy_sells
            await self.copy_manager.save_user_settings(user_id, settings)
            await self._show_settings_menu(query, user_id)
            return

        if action == "multibuy_on":
            is_pro = await self.db.is_pro(user_id)
            if not is_pro:
                keyboard = [
                    [InlineKeyboardButton("💎 Upgrade to PRO", callback_data="menu_upgrade")],
                    [InlineKeyboardButton("⬅️ Back", callback_data="copy_main")],
                ]
                await query.edit_message_text(
                    "🔥 **Multi-Buy Only Mode**\n\n"
                    "This is a PRO feature!\n\n"
                    "Upgrade to PRO to unlock this feature!",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
            await query.edit_message_text("⏳ Enabling Multi-Buy mode...")
            await self.copy_manager.update_setting(user_id, 'multi_buy_only', True)
            await self._show_copytrade_main(query, user_id, edit=True)
            return

        if action == "multibuy_off":
            await query.edit_message_text("⏳ Disabling Multi-Buy mode...")
            await self.copy_manager.update_setting(user_id, 'multi_buy_only', False)
            await self._show_copytrade_main(query, user_id, edit=True)
            return

        if action == "multibuysettings":
            await query.answer("🔍 Debug: reached multibuysettings", show_alert=True)  # temp
            try:
                await self.show_multibuy_settings(query, user_id)
            except Exception as e:
                logger.error(f"show_multi_buy_settings error: {e}", exc_info=True)
                try:
                    await query.edit_message_text(f"⚠️ Error: {e}")
                except Exception as inner:
                    logger.error(f"Failed to display error: {inner}", exc_info=True)
            return


        if action.startswith("multibuythreshold"):
            n = int(action.replace("multibuythreshold", ""))
            await self.copy_manager.update_setting(user_id, 'multibuythreshold', n)
            await self.show_multibuy_settings(query, user_id)
            return

        if action.startswith("multibuysellmode"):
            mode = action.replace("multibuysellmode", "")  # 'any' or 'all'
            await self.copy_manager.update_setting(user_id, 'multibuysellmode', mode)
            await self.show_multibuy_settings(query, user_id)
            return

        if action.startswith("multibuywindow"):
            hours = min(int(action.replace("multibuywindow", "")), 24)
            await self.copy_manager.update_setting(user_id, 'multibuywindow', hours)
            await self.show_multibuy_settings(query, user_id)
            return


        
    
    async def _confirm_enable(self, query, user_id: int):
        """Show confirmation before enabling copy trading"""
        settings = await self.copy_manager.get_user_settings(user_id)
        
        keyboard = [
            [InlineKeyboardButton("✅ Yes, Enable Copy Trading", callback_data="copy_confirm_enable")],
            [InlineKeyboardButton("❌ Cancel", callback_data="copy_main")]
        ]
        
        await query.edit_message_text(
            "⚠️ **Enable Copy Trading?**\n\n"
            "**Current Settings:**\n"
            f"• Buy: {settings.get_buy_display()}\n"
            f"• Sell: {settings.get_sell_display()}\n"
            f"• Price range: ${settings.min_price:.2f} - ${settings.max_price:.2f}\n\n"
            "**Warning:**\n"
            "• Real trades will execute automatically\n"
            "• You may lose money\n"
            "• Make sure your settings are correct\n\n"
            "Do you want to enable copy trading?",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def _show_settings_menu(self, query, user_id: int):
        """Show settings configuration menu - main categories"""
        settings = await self.copy_manager.get_user_settings(user_id)
        
        keyboard = [
            [InlineKeyboardButton(
                f"📈 Buy Settings: {settings.get_buy_display()}", 
                callback_data="copy_buy_settings"
            )],
            [InlineKeyboardButton(
                f"📉 Sell Settings: {settings.get_sell_display()}", 
                callback_data="copy_sell_settings"
            )],
            [InlineKeyboardButton(
                f"🎯 Filters & Limits", 
                callback_data="copy_filter_settings"
            )],
            [InlineKeyboardButton(
                f"{'✅' if settings.copy_buys else '❌'} Copy Buys", 
                callback_data="copy_toggle_buys"
            ),
            InlineKeyboardButton(
                f"{'✅' if settings.copy_sells else '❌'} Copy Sells", 
                callback_data="copy_toggle_sells"
            )],
            [InlineKeyboardButton("⚡ Multi-Buy Settings", 
            callback_data="copy_multibuysettings")],
            [InlineKeyboardButton("⬅️ Back", callback_data="copy_main")]

        ]
        
        await query.edit_message_text(
            "⚙️ **Configure Copy Trading**\n\n"
            "Select a category to modify:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def _show_buy_settings(self, query, user_id: int):
        """Show buy settings submenu"""
        settings = await self.copy_manager.get_user_settings(user_id)
        
        # Determine which type is selected
        is_fixed = settings.buy_amount_type == 'fixed'
        is_percentage = settings.buy_amount_type == 'percentage'
        
        keyboard = [
            # Amount type selection
            [
                InlineKeyboardButton(
                    f"{'✓ ' if is_fixed else ''}💵 Fixed USD", 
                    callback_data="copy_buy_type_fixed"
                ),
                InlineKeyboardButton(
                    f"{'✓ ' if is_percentage else ''}📊 % of Portfolio", 
                    callback_data="copy_buy_type_percentage"
                )
            ],
            # Amount value
            [InlineKeyboardButton(
                f"💰 Amount: {settings.get_buy_display()}", 
                callback_data="copy_set_buy_amount"
            )],
            [InlineKeyboardButton("⬅️ Back to Settings", callback_data="copy_settings")]
        ]
        
        type_desc = "Fixed USD amount per trade" if is_fixed else "Percentage of your portfolio balance"
        
        await query.edit_message_text(
            f"📈 **Buy Settings**\n\n"
            f"**Amount Type:** {'Fixed USD' if is_fixed else '% of Portfolio'}\n"
            f"_{type_desc}_\n\n"
            f"**Current Value:** {settings.get_buy_display()}\n\n"
            f"Select the amount type, then set the value:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def _show_sell_settings(self, query, user_id: int):
        """Show sell settings submenu"""
        settings = await self.copy_manager.get_user_settings(user_id)
        
        # Determine which type is selected
        is_fixed = settings.sell_amount_type == 'fixed'
        is_percentage = settings.sell_amount_type == 'percentage_holdings'
        
        keyboard = [
            # Amount type selection
            [
                InlineKeyboardButton(
                    f"{'✓ ' if is_fixed else ''}💵 Fixed USD", 
                    callback_data="copy_sell_type_fixed"
                ),
                InlineKeyboardButton(
                    f"{'✓ ' if is_percentage else ''}📊 % of Holdings", 
                    callback_data="copy_sell_type_percentage"
                )
            ],
            # Amount value
            [InlineKeyboardButton(
                f"💰 Amount: {settings.get_sell_display()}", 
                callback_data="copy_set_sell_amount"
            )],
            [InlineKeyboardButton("⬅️ Back to Settings", callback_data="copy_settings")]
        ]
        
        type_desc = "Fixed USD amount per trade" if is_fixed else "Percentage of your position in that market"
        
        await query.edit_message_text(
            f"📉 **Sell Settings**\n\n"
            f"**Amount Type:** {'Fixed USD' if is_fixed else '% of Holdings'}\n"
            f"_{type_desc}_\n\n"
            f"**Current Value:** {settings.get_sell_display()}\n\n"
            f"Select the amount type, then set the value:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def _show_filter_settings(self, query, user_id: int):
        """Show filter/limit settings submenu"""
        settings = await self.copy_manager.get_user_settings(user_id)
        
        keyboard = [
            [InlineKeyboardButton(
                f"📉 Min Price: ${settings.min_price:.2f}", 
                callback_data="copy_set_min_price"
            )],
            [InlineKeyboardButton(
                f"📈 Max Price: ${settings.max_price:.2f}", 
                callback_data="copy_set_max_price"
            )],
            [InlineKeyboardButton(
                f"🎯 Min Target Trade: ${settings.min_target_trade_value:.0f}", 
                callback_data="copy_set_min_target"
            )],
            [InlineKeyboardButton("⬅️ Back to Settings", callback_data="copy_settings")]
        ]
        
        await query.edit_message_text(
            f"🎯 **Filters & Limits**\n\n"
            f"**Price Range:** ${settings.min_price:.2f} - ${settings.max_price:.2f}\n"
            f"_Only copy trades within this price range_\n\n"
            f"**Min Target Trade:** ${settings.min_target_trade_value:.0f}\n"
            f"_Only copy if original trade is at least this size_\n\n"
            f"Select a setting to modify:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def show_multibuy_settings(self, query, user_id: int):
        settings = await self.copy_manager.get_user_settings(user_id)
        t = getattr(settings, 'multibuythreshold', 2)
        m = getattr(settings, 'multibuysellmode', 'any')
        w = getattr(settings, 'multibuywindow', 1)

        threshold_row = [
            InlineKeyboardButton(
                f"{'✅ ' if t == n else ''}{n} Wallets",
                callback_data=f"copy_multibuythreshold{n}"
            )
            for n in [2, 3, 4, 5]
        ]
        sell_row = [
            InlineKeyboardButton("✅ Any Sells" if m == 'any' else "Any Sells", callback_data="copy_multibuysellmodeany"),
            InlineKeyboardButton("✅ All Sell"  if m == 'all' else "All Sell",  callback_data="copy_multibuysellmodeall"),
        ]
        window_row1 = [
            InlineKeyboardButton(f"{'✅ ' if w == h else ''}{h}h", callback_data=f"copy_multibuywindow{h}")
            for h in [1, 2, 4, 6]
        ]
        window_row2 = [
            InlineKeyboardButton(f"{'✅ ' if w == h else ''}{h}h", callback_data=f"copy_multibuywindow{h}")
            for h in [8, 12, 18, 24]
        ]

        keyboard = [
            threshold_row, sell_row, window_row1, window_row2,
            [InlineKeyboardButton("🔙 Back to Settings", callback_data="copy_settings")]
        ]
        sell_text = "any tracked wallet sells" if m == 'any' else "all tracked wallets sell"
        await query.edit_message_text(
            f"⚡ <b>Multi-Buy Settings</b>\n\n"
            f"<b>Required Wallets:</b> {t} wallets must buy to trigger\n\n"
            f"<b>Time Window:</b> Within the last {w}h\n\n"
            f"<b>Sell Trigger:</b> Copy sell when <b>{sell_text}</b>\n\n"
            f"<i>Fires when {t}+ tracked wallets buy the same market within {w}h.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    
    async def _handle_setting_selection(self, query, user_id: int, action: str):
        """Handle when user selects a setting to modify"""
        setting_name = action.replace("set_", "")
        settings = await self.copy_manager.get_user_settings(user_id)
        
        if setting_name == "buy_amount":
            is_fixed = settings.buy_amount_type == 'fixed'
            current = settings.buy_amount_value
            
            if is_fixed:
                presets = [10, 25, 50, 100, 250, 500]
                keyboard = self._build_preset_keyboard(
                    presets, "buy_amount", current, prefix="$", suffix=""
                )
                text = (
                    f"💰 **Buy Amount (Fixed)**\n\n"
                    f"Current: **${current:.0f}**\n\n"
                    f"Fixed USD amount per buy trade.\n"
                    f"Range: ${SettingsLimits.BUY_FIXED_MIN:.0f} - ${SettingsLimits.BUY_FIXED_MAX:,.0f}\n\n"
                    f"Select a preset or enter custom:"
                )
            else:
                presets = [5, 10, 15, 20, 25, 50]
                keyboard = self._build_preset_keyboard(
                    presets, "buy_amount", current, prefix="", suffix="%"
                )
                text = (
                    f"💰 **Buy Amount (% of Portfolio)**\n\n"
                    f"Current: **{current:.0f}%**\n\n"
                    f"Percentage of your balance per buy trade.\n"
                    f"Range: {SettingsLimits.BUY_PCT_MIN:.0f}% - {SettingsLimits.BUY_PCT_MAX:.0f}%\n\n"
                    f"Select a preset or enter custom:"
                )
            
            # Add back button to buy settings
            keyboard.append([InlineKeyboardButton("⬅️ Back to Buy Settings", callback_data="copy_buy_settings")])
        
        elif setting_name == "sell_amount":
            is_fixed = settings.sell_amount_type == 'fixed'
            current = settings.sell_amount_value
            
            if is_fixed:
                presets = [10, 25, 50, 100, 250, 500]
                keyboard = self._build_preset_keyboard(
                    presets, "sell_amount", current, prefix="$", suffix=""
                )
                text = (
                    f"💰 **Sell Amount (Fixed)**\n\n"
                    f"Current: **${current:.0f}**\n\n"
                    f"Fixed USD amount per sell trade.\n"
                    f"Range: ${SettingsLimits.SELL_FIXED_MIN:.0f} - ${SettingsLimits.SELL_FIXED_MAX:,.0f}\n\n"
                    f"Select a preset or enter custom:"
                )
            else:
                presets = [25, 50, 75, 100]
                keyboard = self._build_preset_keyboard(
                    presets, "sell_amount", current, prefix="", suffix="%"
                )
                text = (
                    f"💰 **Sell Amount (% of Holdings)**\n\n"
                    f"Current: **{current:.0f}%**\n\n"
                    f"Percentage of your position to sell.\n"
                    f"100% = sell entire position (full exit)\n"
                    f"Range: {SettingsLimits.SELL_PCT_MIN:.0f}% - {SettingsLimits.SELL_PCT_MAX:.0f}%\n\n"
                    f"Select a preset or enter custom:"
                )
            
            # Add back button to sell settings
            keyboard.append([InlineKeyboardButton("⬅️ Back to Sell Settings", callback_data="copy_sell_settings")])
        
        elif setting_name == "min_price":
            current = settings.min_price
            presets = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
            keyboard = self._build_preset_keyboard(
                presets, "min_price", current, prefix="$", suffix="", decimals=2
            )
            keyboard.append([InlineKeyboardButton("⬅️ Back to Filters", callback_data="copy_filter_settings")])
            text = (
                f"📉 **Minimum Price**\n\n"
                f"Current: **${current:.2f}**\n\n"
                f"Only copy trades with price above this.\n"
                f"Range: ${SettingsLimits.PRICE_MIN:.2f} - ${SettingsLimits.PRICE_MAX:.2f}\n\n"
                f"Select a preset or enter custom:"
            )
        
        elif setting_name == "max_price":
            current = settings.max_price
            presets = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
            keyboard = self._build_preset_keyboard(
                presets, "max_price", current, prefix="$", suffix="", decimals=2
            )
            keyboard.append([InlineKeyboardButton("⬅️ Back to Filters", callback_data="copy_filter_settings")])
            text = (
                f"📈 **Maximum Price**\n\n"
                f"Current: **${current:.2f}**\n\n"
                f"Only copy trades with price below this.\n"
                f"Range: ${SettingsLimits.PRICE_MIN:.2f} - ${SettingsLimits.PRICE_MAX:.2f}\n\n"
                f"Select a preset or enter custom:"
            )
        
        elif setting_name == "min_target":
            current = settings.min_target_trade_value
            presets = [50, 100, 250, 500, 1000, 2500]
            keyboard = self._build_preset_keyboard(
                presets, "min_target", current, prefix="$", suffix=""
            )
            keyboard.append([InlineKeyboardButton("⬅️ Back to Filters", callback_data="copy_filter_settings")])
            text = (
                f"🎯 **Minimum Target Trade Value**\n\n"
                f"Current: **${current:.0f}**\n\n"
                f"Only copy trades where the original trade is at least this size.\n"
                f"Range: ${SettingsLimits.MIN_TARGET_VALUE_MIN:.0f} - ${SettingsLimits.MIN_TARGET_VALUE_MAX:,.0f}\n\n"
                f"Select a preset or enter custom:"
            )
        
        # Legacy settings (kept for compatibility)
        elif setting_name == "max_trade_size":
            current = settings.max_trade_size
            presets = [25, 50, 100, 250, 500, 1000]
            keyboard = self._build_preset_keyboard(
                presets, "max_trade_size", current, prefix="$", suffix=""
            )
            text = (
                f"💰 **Max Trade Size**\n\n"
                f"Current: **${current:.0f}**\n\n"
                f"Maximum amount per copy trade.\n"
                f"Range: ${SettingsLimits.MAX_TRADE_SIZE_MIN:.0f} - ${SettingsLimits.MAX_TRADE_SIZE_MAX:,.0f}\n\n"
                f"Select a preset or enter custom:"
            )
        
        elif setting_name == "portfolio_pct":
            current = settings.portfolio_percentage
            presets = [5, 10, 15, 20, 25, 50]
            keyboard = self._build_preset_keyboard(
                presets, "portfolio_pct", current, prefix="", suffix="%"
            )
            text = (
                f"📊 **Portfolio Percentage**\n\n"
                f"Current: **{current:.0f}%**\n\n"
                f"Percentage of your balance per trade.\n"
                f"Range: {SettingsLimits.PORTFOLIO_PCT_MIN:.0f}% - {SettingsLimits.PORTFOLIO_PCT_MAX:.0f}%\n\n"
                f"Select a preset or enter custom:"
            )
        
        else:
            await query.edit_message_text("❌ Unknown setting.")
            return
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    def _build_preset_keyboard(
        self, 
        presets: list, 
        setting_name: str, 
        current_value: float,
        prefix: str = "",
        suffix: str = "",
        decimals: int = 0
    ) -> list:
        """Build keyboard with preset values"""
        keyboard = []
        row = []
        
        for i, preset in enumerate(presets):
            if decimals > 0:
                label = f"{prefix}{preset:.{decimals}f}{suffix}"
            else:
                label = f"{prefix}{preset:.0f}{suffix}"
            
            # Mark current value
            if abs(preset - current_value) < 0.01:
                label = f"✓ {label}"
            
            row.append(InlineKeyboardButton(
                label,
                callback_data=f"copy_val_{setting_name}_{preset}"
            ))
            
            if len(row) == 3:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        # Custom button
        keyboard.append([InlineKeyboardButton("✏️ Custom Value", callback_data=f"copy_custom_{setting_name}")])
        
        return keyboard
    
    async def _handle_preset_value(self, query, user_id: int, action: str):
        """Handle preset value selection"""
        # Parse: val_setting_name_value
        parts = action.replace("val_", "").rsplit("_", 1)
        if len(parts) != 2:
            await query.edit_message_text("❌ Invalid selection.")
            return
        
        setting_name, value_str = parts
        
        try:
            value = float(value_str)
        except ValueError:
            await query.edit_message_text("❌ Invalid value.")
            return
        
        # Map setting names to CopyTradeSettings attributes
        attr_map = {
            "max_trade_size": "max_trade_size",
            "portfolio_pct": "portfolio_percentage",
            "buy_amount": "buy_amount_value",
            "sell_amount": "sell_amount_value",
            "min_price": "min_price",
            "max_price": "max_price",
            "min_target": "min_target_trade_value"
        }
        
        attr_name = attr_map.get(setting_name)
        if not attr_name:
            await query.edit_message_text("❌ Unknown setting.")
            return
        
        # Validate and save
        valid, error = await self._validate_setting(setting_name, value, user_id)
        if not valid:
            await query.answer(error, show_alert=True)
            return
        
        await self.copy_manager.update_setting(user_id, attr_name, value)
        
        await query.answer(f"✅ Updated!")
        
        # Navigate back to appropriate menu
        if setting_name in ["buy_amount"]:
            await self._show_buy_settings(query, user_id)
        elif setting_name in ["sell_amount"]:
            await self._show_sell_settings(query, user_id)
        elif setting_name in ["min_price", "max_price", "min_target"]:
            await self._show_filter_settings(query, user_id)
        else:
            await self._show_settings_menu(query, user_id)
    
    async def _start_custom_input(self, query, user_id: int, setting_name: str):
        """Start custom value input conversation"""
        self._pending_settings[user_id] = setting_name
        settings = await self.copy_manager.get_user_settings(user_id)
        
        # Get setting info for the prompt
        prompts = {
            "max_trade_size": f"Enter max trade size (${SettingsLimits.MAX_TRADE_SIZE_MIN:.0f} - ${SettingsLimits.MAX_TRADE_SIZE_MAX:,.0f}):",
            "portfolio_pct": f"Enter portfolio percentage ({SettingsLimits.PORTFOLIO_PCT_MIN:.0f} - {SettingsLimits.PORTFOLIO_PCT_MAX:.0f}):",
            "min_price": f"Enter minimum price (${SettingsLimits.PRICE_MIN:.2f} - ${SettingsLimits.PRICE_MAX:.2f}):",
            "max_price": f"Enter maximum price (${SettingsLimits.PRICE_MIN:.2f} - ${SettingsLimits.PRICE_MAX:.2f}):",
            "min_target": f"Enter minimum target value (${SettingsLimits.MIN_TARGET_VALUE_MIN:.0f} - ${SettingsLimits.MIN_TARGET_VALUE_MAX:,.0f}):"
        }
        
        # Dynamic prompts for buy/sell based on type
        if setting_name == "buy_amount":
            if settings.buy_amount_type == 'fixed':
                prompts["buy_amount"] = f"Enter buy amount in USD (${SettingsLimits.BUY_FIXED_MIN:.0f} - ${SettingsLimits.BUY_FIXED_MAX:,.0f}):"
            else:
                prompts["buy_amount"] = f"Enter buy percentage ({SettingsLimits.BUY_PCT_MIN:.0f} - {SettingsLimits.BUY_PCT_MAX:.0f}):"
        
        if setting_name == "sell_amount":
            if settings.sell_amount_type == 'fixed':
                prompts["sell_amount"] = f"Enter sell amount in USD (${SettingsLimits.SELL_FIXED_MIN:.0f} - ${SettingsLimits.SELL_FIXED_MAX:,.0f}):"
            else:
                prompts["sell_amount"] = f"Enter sell percentage ({SettingsLimits.SELL_PCT_MIN:.0f} - {SettingsLimits.SELL_PCT_MAX:.0f}):"
        
        prompt = prompts.get(setting_name, "Enter value:")
        
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="copy_settings")]]
        
        await query.edit_message_text(
            f"✏️ **Custom Value**\n\n{prompt}\n\n_Send /cancel to abort_",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return AWAITING_CUSTOM_SETTING
    
    async def receive_custom_setting(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive custom setting value"""
        user_id = update.effective_user.id
        text = update.message.text.strip()
        
        if user_id not in self._pending_settings:
            # ✅ ADD KEYBOARD
            keyboard = [[InlineKeyboardButton("⚙️ Copy Trading Settings", callback_data="copy_settings")]]
            
            await update.message.reply_text(
                "⏳ **Session Expired**\n\n"
                "Use /copytrade to try again.",
                reply_markup=InlineKeyboardMarkup(keyboard)  # ✅ ADD THIS
            )
            return ConversationHandler.END
        
        setting_name = self._pending_settings[user_id]
        
        # Parse value
        try:
            # Remove common prefixes/suffixes
            clean_text = text.replace('$', '').replace('%', '').replace(',', '').strip()
            value = float(clean_text)
        except ValueError:
            # ✅ ADD KEYBOARD
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="copy_settings")]]
            
            await update.message.reply_text(
                "❌ **Invalid Number**\n\n"
                "Please enter a valid number.\n\n"
                "_Or click Cancel below_",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)  # ✅ ADD THIS
            )
            return AWAITING_CUSTOM_SETTING
        
        # Validate
        valid, error = await self._validate_setting(setting_name, value, user_id)
        if not valid:
            # ✅ ADD KEYBOARD
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="copy_settings")]]
            
            await update.message.reply_text(
                f"❌ **Invalid Value**\n\n"
                f"{error}\n\n"
                f"_Try again or click Cancel below_",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)  # ✅ ADD THIS
            )
            return AWAITING_CUSTOM_SETTING
        
        # Map setting names to CopyTradeSettings attributes
        attr_map = {
            "max_trade_size": "max_trade_size",
            "portfolio_pct": "portfolio_percentage",
            "buy_amount": "buy_amount_value",
            "sell_amount": "sell_amount_value",
            "min_price": "min_price",
            "max_price": "max_price",
            "min_target": "min_target_trade_value"
        }
        
        attr_name = attr_map.get(setting_name)
        if not attr_name:
            # ✅ ADD KEYBOARD
            keyboard = [[InlineKeyboardButton("⚙️ Back to Settings", callback_data="copy_settings")]]
            
            await update.message.reply_text(
                "❌ Unknown setting.",
                reply_markup=InlineKeyboardMarkup(keyboard)  # ✅ ADD THIS
            )
            del self._pending_settings[user_id]
            return ConversationHandler.END
        
        # Save
        await self.copy_manager.update_setting(user_id, attr_name, value)
        del self._pending_settings[user_id]
        
        # Format display based on setting type
        settings = await self.copy_manager.get_user_settings(user_id)
        
        display_map = {
            "max_trade_size": f"${value:.0f}",
            "portfolio_pct": f"{value:.0f}%",
            "min_price": f"${value:.2f}",
            "max_price": f"${value:.2f}",
            "min_target": f"${value:.0f}"
        }
        
        if setting_name == "buy_amount":
            if settings.buy_amount_type == 'fixed':
                display = f"${value:.0f}"
            else:
                display = f"{value:.0f}%"
        elif setting_name == "sell_amount":
            if settings.sell_amount_type == 'fixed':
                display = f"${value:.0f}"
            else:
                display = f"{value:.0f}%"
        else:
            display = display_map.get(setting_name, str(value))
        
        keyboard = [
            [InlineKeyboardButton("⚙️ Back to Settings", callback_data="copy_settings")],
            [InlineKeyboardButton("🤖 Back to Copy Trading", callback_data="copy_main")]
        ]
        
        # The setting is already saved — always end the conversation.
        # Wrap the reply in try/except so a transient Telegram network error
        # (httpx.ConnectError → telegram.NetworkError) doesn't crash the handler.
        try:
            await update.message.reply_text(
                f"✅ **Setting Updated!**\n\nNew value: **{display}**",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except (NetworkError, TimedOut) as net_err:
            logger.warning(
                f"Could not send setting-update confirmation to user {user_id} "
                f"(network error, setting was saved): {net_err}"
            )
        
        return ConversationHandler.END

    async def _validate_setting(self, setting_name: str, value: float, user_id: int = None) -> tuple[bool, str]:
        """Validate a setting value"""
        if setting_name == "max_trade_size":
            return CopyTradeSettings.validate_max_trade_size(value)
        elif setting_name == "portfolio_pct":
            return CopyTradeSettings.validate_portfolio_percentage(value)
        elif setting_name == "buy_amount":
            # Need to check the type to validate correctly
            if user_id:
                settings = await self.copy_manager.get_user_settings(user_id)
                if settings.buy_amount_type == 'fixed':
                    return CopyTradeSettings.validate_buy_fixed(value)
                else:
                    return CopyTradeSettings.validate_buy_percentage(value)
            return True, ""
        elif setting_name == "sell_amount":
            if user_id:
                settings = await self.copy_manager.get_user_settings(user_id)
                if settings.sell_amount_type == 'fixed':
                    return CopyTradeSettings.validate_sell_fixed(value)
                else:
                    return CopyTradeSettings.validate_sell_percentage(value)
            return True, ""
        elif setting_name == "min_price":
            return CopyTradeSettings.validate_price(value, is_min=True)
        elif setting_name == "max_price":
            return CopyTradeSettings.validate_price(value, is_min=False)
        elif setting_name == "min_target":
            return CopyTradeSettings.validate_min_target_value(value)
        return True, ""
    
    async def _show_history(self, query, user_id: int):
        """Show copy trade history"""
        history = await self.db.get_copy_trade_history(user_id, limit=10)
        stats = await self.db.get_copy_trade_stats(user_id)
        
        if not history:
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="copy_main")]]
            await query.edit_message_text(
                "📊 **Copy Trade History**\n\n"
                "No trades yet.\n\n"
                "Enable copy trading and track some wallets to get started!",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        message = f"📈 **Copy Trade History**\n\n"
        message += f"**Stats:**\n"
        message += f"• Total: {stats['total_trades']}\n"
        message += f"• Successful: {stats['successful_trades']}\n"
        message += f"• Failed: {stats['failed_trades']}\n"
        message += f"**Recent Trades:**\n\n"
        
        for trade in history:
            emoji = "✅" if trade.get('success') else "❌"
            market = trade.get('market', 'Unknown')[:30]
            side = trade.get('side', 'Unknown')
            amount = trade.get('amount', 0)
            timestamp = trade.get('timestamp', '')
            
            message += f"{emoji} **{market}**\n"
            message += f"   {side} ${amount:.2f}"
            if timestamp:
                message += f" • {timestamp}"
            message += "\n\n"
        
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="copy_main")]]
        
        await query.edit_message_text(
            message,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def cancel_conversation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel conversation"""
        user_id = update.effective_user.id
        self._pending_imports.pop(user_id, None)
        self._pending_withdraws.pop(user_id, None)
        self._pending_settings.pop(user_id, None)
        
        # ✅ ADD KEYBOARD
        keyboard = [
            [InlineKeyboardButton("💳 Wallet", callback_data="menu_trading_wallet")],
            [InlineKeyboardButton("🤖 Copy Trading", callback_data="menu_copytrade")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
        ]
        
        await update.message.reply_text(
            "❌ **Cancelled**",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)  # ✅ ADD THIS
        )
        return ConversationHandler.END
    
    def get_handlers(self):
        """Return handlers"""
        
        withdraw_conv = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(self.wallet_callback, pattern="^wallet_withdraw$")
            ],
            states={
                AWAITING_WITHDRAW_ADDRESS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_withdraw_address)
                ],
                AWAITING_WITHDRAW_AMOUNT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_withdraw_amount)
                ]
            },
            fallbacks=[CommandHandler("cancel", self.cancel_conversation)]
        )
        
        custom_setting_conv = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(self.copytrade_callback, pattern="^copy_custom_")
            ],
            states={
                AWAITING_CUSTOM_SETTING: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_custom_setting)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.cancel_conversation),
                CallbackQueryHandler(self.copytrade_callback, pattern="^copy_settings$")
            ]
        )
        
        return [
            CommandHandler("wallet", self.wallet_command),
            withdraw_conv,
            custom_setting_conv,
            CallbackQueryHandler(self.wallet_callback, pattern="^wallet_"),
            CommandHandler("copytrade", self.copytrade_command),
            CallbackQueryHandler(self.copytrade_callback, pattern="^copy_"),
        ]