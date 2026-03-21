# bot/trading/__init__.py
"""Trading module for Polymarket copy trading"""

from .wallet_manager import WalletManager
from .copy_trader import CopyTrader, CopyTradeSettings, CopyTradeManager

__all__ = [
    'WalletManager',
    'CopyTrader',
    'CopyTradeSettings',
    'CopyTradeManager',
]
