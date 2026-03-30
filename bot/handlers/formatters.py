"""Formatting functions for bot messages."""

from bot.services.database import WalletType


def format_wallet_stats(wallet_address: str, stats: dict, custom_name: str = None) -> str:
    """Format wallet statistics for display."""
    short_address = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    
    # Use custom name if provided, otherwise API name, otherwise short address
    raw_name = custom_name or stats.get('name') or short_address
    name = escape_markdown(raw_name)  # Escape the name
    
    # Format ROI with emoji
    roi = stats.get('roi_all_time', 0)
    roi_emoji = "🟢" if roi >= 0 else "🔴"
    roi_str = f"{roi_emoji} {roi:+.2f}%"
    
    # Format PnL (cash-flow: realized net cash + current portfolio value)
    pnl = stats.get('pnl_all_time', 0)
    realized_pnl = stats.get('realized_pnl', 0)
    open_pnl = stats.get('open_pnl', 0)
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    pnl_str = f"{pnl_emoji} ${pnl:+,.2f}"
    realized_emoji = "🟢" if realized_pnl >= 0 else "🔴"
    realized_str = f"{realized_emoji} ${realized_pnl:+,.2f}"
    portfolio_str = f"${open_pnl:,.2f}"
    
    # Format win rate
    win_rate = stats.get('win_rate')
    if win_rate is not None:
        win_emoji = "🎯" if win_rate >= 50 else "📉"
        winning = stats.get('winning_positions', 0)
        losing = stats.get('losing_positions', 0)
        win_rate_str = f"{win_emoji} {win_rate:.1f}% ({winning}W / {losing}L)"
    else:
        win_rate_str = "⏳ No resolved positions"
    
    # Format volume
    volume_7d = stats.get('volume_7d', 0)
    volume_str = f"${volume_7d:,.2f}"
    
    # Format total trades - show "10,000+" if capped
    total_trades = stats.get('total_trades', 0)
    trades_display = f"{total_trades:,}+" if stats.get('trades_capped') else f"{total_trades:,}"
    
    total_positions = stats.get('total_positions', 0)
    
    message = f"""📊 **Wallet Statistics**

👤 **{name}**
`{wallet_address}`

**Wallet Performance:**
• ROI (All-Time): {roi_str}
• Total PnL: {pnl_str}
  ├ Realized: {realized_str}
  └ Open Positions: {portfolio_str}
• Win Rate: {win_rate_str}

**Activity:**
• 7d Trading Vol: {volume_str}
• Total Trades: {trades_display}
• Total Positions: {total_positions:,}"""

    return message


def format_wallet_stats_compact(
    wallet_address: str, 
    stats: dict, 
    custom_name: str = None, 
    wallet_type: str = None,
    stored_name: str = None
) -> str:
    """Format wallet statistics in a compact format for lists."""
    short_address = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    
    raw_name = custom_name or stored_name or stats.get('name') or short_address
    name = escape_markdown(raw_name)  # Escape the name
    
    badge = "⭐ " if wallet_type == WalletType.TAGWISE.value else ""
    
    roi = stats.get('roi_all_time', 0)
    roi_emoji = "🟢" if roi >= 0 else "🔴"
    
    pnl = stats.get('pnl_all_time', 0)
    pnl_str = f"${pnl:+,.0f}"
    
    win_rate = stats.get('win_rate')
    win_rate_str = f"{win_rate:.0f}%" if win_rate is not None else "N/A"
    
    volume_7d = stats.get('volume_7d', 0)
    
    # Format total trades - show "10,000+" if capped
    total_trades = stats.get('total_trades', 0)
    trades_display = f"{total_trades:,}+" if stats.get('trades_capped') else f"{total_trades:,}"
    
    return f"""• {badge}**{name}**
  `{short_address}`
  {roi_emoji} ROI: {roi:+.1f}% | PnL: {pnl_str} | Win: {win_rate_str}
  7d Vol: ${volume_7d:,.0f} | Trades: {trades_display}
"""


def format_top_trader(rank: int, trader: dict) -> str:
    """Format a top trader entry for display."""
    address = trader.get('address', '')
    name = trader.get('display_name') or trader.get('username') or f"{address[:6]}...{address[-4:]}"
    
    # Escape the name to prevent Markdown parsing issues
    name = escape_markdown(name)
    
    verified = " ✅" if trader.get('verified') else ""
    
    # Escape X username as well
    x_username = trader.get('x_username', '')
    x_handle = f" (@{escape_markdown(x_username)})" if x_username else ""
    
    pnl = trader.get('pnl', 0)
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    
    volume = trader.get('volume', 0)
    
    return f"""**#{rank}** {name}{verified}{x_handle}
`{address}`
{pnl_emoji} PnL: ${pnl:+,.0f} | Vol: ${volume:,.0f}
"""


def escape_markdown(text: str) -> str:
    """Escape special Markdown characters."""
    if not text:
        return text
    
    # Escape all special Markdown characters
    special_chars = ['_', '*', '`', '[', ']', '(', ')']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text