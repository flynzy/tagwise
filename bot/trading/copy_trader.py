# bot/trading/copy_trader.py
"""
Copy trading with gasless Safe wallets.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List
from enum import Enum
import math
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    BalanceAllowanceParams,
    AssetType,
    OrderArgs,             # add
    PartialCreateOrderOptions,  # add
    OrderType,
    MarketOrderArgs
)
from py_clob_client.order_builder.constants import BUY as BUY_SIDE

logger = logging.getLogger(__name__)


def _get_builder_config():
    """Builder order attribution is handled by BuilderRelayer (builder_relayer.py),
    not by ClobClient. The Python py-clob-client does not accept a builder_config
    parameter — that is a TypeScript-only feature. This function is kept as a
    no-op stub in case future py-clob-client versions add support."""
    return None


class BuyAmountType(Enum):
    FIXED = "fixed"              # Fixed USD amount
    PERCENTAGE = "percentage"    # % of available USDC cash


class SellAmountType(Enum):
    FIXED = "fixed"                        # Fixed USD amount
    PERCENTAGE_HOLDINGS = "percentage_holdings"  # % of current holdings in that market


# Validation limits for copy trade settings
class SettingsLimits:
    # Legacy limits
    MAX_TRADE_SIZE_MIN = 0.1
    MAX_TRADE_SIZE_MAX = 10000.0

    PORTFOLIO_PCT_MIN = 1.0
    PORTFOLIO_PCT_MAX = 100.0

    # Buy settings limits
    BUY_FIXED_MIN = 1.0
    BUY_FIXED_MAX = 10000.0
    BUY_PCT_MIN = 1.0
    BUY_PCT_MAX = 100.0

    # Sell settings limits
    SELL_FIXED_MIN = 1.0
    SELL_FIXED_MAX = 10000.0
    SELL_PCT_MIN = 1.0
    SELL_PCT_MAX = 100.0

    # Price limits
    PRICE_MIN = 0.01
    PRICE_MAX = 0.99

    MIN_TARGET_VALUE_MIN = MAX_TRADE_SIZE_MIN
    MIN_TARGET_VALUE_MAX = MAX_TRADE_SIZE_MAX


class CopyTradeSettings:
    """Settings for copy trading"""

    def __init__(
        self,
        enabled: bool = False,
        # Legacy fields (kept for compatibility)
        max_trade_size: float = 50.0,
        portfolio_percentage: float = 10.0,
        # Buy settings
        buy_amount_type: str = 'percentage',
        buy_amount_value: float = 10.0,
        # Sell settings
        sell_amount_type: str = 'percentage_holdings',
        sell_amount_value: float = 100.0,
        # Other settings
        min_price: float = 0.05,
        max_price: float = 0.95,
        min_target_trade_value: float = 100.0,
        copy_buys: bool = True,
        copy_sells: bool = True,
        # Multi-buy only mode (PRO feature)
        multi_buy_only: bool = False,
        multibuythreshold: int = 2,
        multibuysellmode: str = 'any',
        multibuywindow: int = 1, 
    ):
        self.enabled = enabled
        # Legacy
        self.max_trade_size = max_trade_size
        self.portfolio_percentage = portfolio_percentage
        # Buy settings
        self.buy_amount_type = buy_amount_type
        self.buy_amount_value = buy_amount_value
        # Sell settings
        self.sell_amount_type = sell_amount_type
        self.sell_amount_value = sell_amount_value
        # Other
        self.min_price = min_price
        self.max_price = max_price
        self.min_target_trade_value = min_target_trade_value
        self.copy_buys = copy_buys
        self.copy_sells = copy_sells
        # Multi-buy only
        self.multi_buy_only = bool(multi_buy_only)
        self.multibuythreshold = int(multibuythreshold)
        self.multibuysellmode = str(multibuysellmode)
        self.multibuywindow = int(multibuywindow)

    @property
    def portfolio_percentage_decimal(self) -> float:
        """Get portfolio percentage as decimal for calculations"""
        return self.portfolio_percentage / 100.0

    @property
    def buy_percentage_decimal(self) -> float:
        """Get buy percentage as decimal (of available USDC cash)"""
        return self.buy_amount_value / 100.0

    @property
    def sell_percentage_decimal(self) -> float:
        """Get sell percentage as decimal"""
        return self.sell_amount_value / 100.0

    def get_buy_display(self) -> str:
        """Get formatted buy settings display"""
        if self.buy_amount_type == 'fixed':
            return f"${self.buy_amount_value:.0f} (Fixed)"
        else:
            return f"{self.buy_amount_value:.0f}% of USDC Balance"

    def get_sell_display(self) -> str:
        """Get formatted sell settings display"""
        if self.sell_amount_type == 'fixed':
            return f"${self.sell_amount_value:.0f} (Fixed)"
        else:
            return f"{self.sell_amount_value:.0f}% of Holdings"

    def to_dict(self) -> Dict:
        return {
            'enabled': self.enabled,
            'mode': 'live',
            # Legacy
            'max_trade_size': self.max_trade_size,
            'portfolio_percentage': self.portfolio_percentage,
            # Buy settings
            'buy_amount_type': self.buy_amount_type,
            'buy_amount_value': self.buy_amount_value,
            # Sell settings
            'sell_amount_type': self.sell_amount_type,
            'sell_amount_value': self.sell_amount_value,
            # Other
            'min_price': self.min_price,
            'max_price': self.max_price,
            'min_target_trade_value': self.min_target_trade_value,
            'copy_buys': self.copy_buys,
            'copy_sells': self.copy_sells,
            # Multi-buy only
            'multi_buy_only': self.multi_buy_only,
            'multibuythreshold': self.multibuythreshold,
            'multibuysellmode': self.multibuysellmode,
            'multibuywindow': self.multibuywindow,

        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'CopyTradeSettings':
        return cls(
            enabled=data.get('enabled', False),
            max_trade_size=data.get('max_trade_size', 50.0),
            portfolio_percentage=data.get('portfolio_percentage', 10.0),
            buy_amount_type=data.get('buy_amount_type', 'percentage'),
            buy_amount_value=data.get('buy_amount_value', 10.0),
            sell_amount_type=data.get('sell_amount_type', 'percentage_holdings'),
            sell_amount_value=data.get('sell_amount_value', 100.0),
            min_price=data.get('min_price', 0.05),
            max_price=data.get('max_price', 0.95),
            min_target_trade_value=data.get('min_target_trade_value', 100.0),
            copy_buys=data.get('copy_buys', True),
            copy_sells=data.get('copy_sells', True),
            multi_buy_only=data.get('multi_buy_only', False),
            multibuythreshold=data.get('multibuythreshold', 2), 
            multibuysellmode=data.get('multibuysellmode', 'any'),
            multibuywindow=data.get('multibuywindow', 1),
        )

    # Validation methods
    @staticmethod
    def validate_max_trade_size(value: float) -> tuple[bool, str]:
        if value < SettingsLimits.MAX_TRADE_SIZE_MIN:
            return False, f"Minimum is ${SettingsLimits.MAX_TRADE_SIZE_MIN:.0f}"
        if value > SettingsLimits.MAX_TRADE_SIZE_MAX:
            return False, f"Maximum is ${SettingsLimits.MAX_TRADE_SIZE_MAX:,.0f}"
        return True, ""

    @staticmethod
    def validate_portfolio_percentage(value: float) -> tuple[bool, str]:
        if value < SettingsLimits.PORTFOLIO_PCT_MIN:
            return False, f"Minimum is {SettingsLimits.PORTFOLIO_PCT_MIN:.0f}%"
        if value > SettingsLimits.PORTFOLIO_PCT_MAX:
            return False, f"Maximum is {SettingsLimits.PORTFOLIO_PCT_MAX:.0f}%"
        return True, ""

    @staticmethod
    def validate_buy_fixed(value: float) -> tuple[bool, str]:
        if value < SettingsLimits.BUY_FIXED_MIN:
            return False, f"Minimum is ${SettingsLimits.BUY_FIXED_MIN:.0f}"
        if value > SettingsLimits.BUY_FIXED_MAX:
            return False, f"Maximum is ${SettingsLimits.BUY_FIXED_MAX:,.0f}"
        return True, ""

    @staticmethod
    def validate_buy_percentage(value: float) -> tuple[bool, str]:
        if value < SettingsLimits.BUY_PCT_MIN:
            return False, f"Minimum is {SettingsLimits.BUY_PCT_MIN:.0f}%"
        if value > SettingsLimits.BUY_PCT_MAX:
            return False, f"Maximum is {SettingsLimits.BUY_PCT_MAX:.0f}%"
        return True, ""

    @staticmethod
    def validate_sell_fixed(value: float) -> tuple[bool, str]:
        if value < SettingsLimits.SELL_FIXED_MIN:
            return False, f"Minimum is ${SettingsLimits.SELL_FIXED_MIN:.0f}"
        if value > SettingsLimits.SELL_FIXED_MAX:
            return False, f"Maximum is ${SettingsLimits.SELL_FIXED_MAX:,.0f}"
        return True, ""

    @staticmethod
    def validate_sell_percentage(value: float) -> tuple[bool, str]:
        if value < SettingsLimits.SELL_PCT_MIN:
            return False, f"Minimum is {SettingsLimits.SELL_PCT_MIN:.0f}%"
        if value > SettingsLimits.SELL_PCT_MAX:
            return False, f"Maximum is {SettingsLimits.SELL_PCT_MAX:.0f}%"
        return True, ""

    @staticmethod
    def validate_price(value: float, is_min: bool = True) -> tuple[bool, str]:
        if value < SettingsLimits.PRICE_MIN:
            return False, f"Minimum is ${SettingsLimits.PRICE_MIN:.2f}"
        if value > SettingsLimits.PRICE_MAX:
            return False, f"Maximum is ${SettingsLimits.PRICE_MAX:.2f}"
        return True, ""

    @staticmethod
    def validate_min_target_value(value: float) -> tuple[bool, str]:
        if value < SettingsLimits.MIN_TARGET_VALUE_MIN:
            return False, f"Minimum is ${SettingsLimits.MIN_TARGET_VALUE_MIN:.0f}"
        if value > SettingsLimits.MIN_TARGET_VALUE_MAX:
            return False, f"Maximum is ${SettingsLimits.MIN_TARGET_VALUE_MAX:,.0f}"
        return True, ""


class CopyTrader:
    """Handles copy trading for a single user with Safe wallet"""

    HOST = "https://clob.polymarket.com"
    CHAIN_ID = 137

    def __init__(
        self,
        clob_client: ClobClient,
        safe_address: str,
        settings: CopyTradeSettings = None,
        wallet_manager=None,
        user_id: int = None,
    ):
        self._client = clob_client
        self.safe_address = safe_address
        self.settings = settings or CopyTradeSettings()
        self._wallet_manager = wallet_manager
        self._user_id = user_id

        self._balance_cache = None
        self._balance_cache_time = 0

    @property
    def client(self) -> ClobClient:
        """Return the pre-configured CLOB client (Privy-backed)."""
        return self._client

    async def get_balance(self, force_refresh: bool = False) -> float:
        """Get available USDC cash balance for trading.

        Delegates to wallet_manager._get_polymarket_balance() when available
        because that method is already proven to work (it powers the /wallet
        balance display).  Fallback to direct CLOB call only if no wallet_manager
        is attached (e.g. in tests).
        """
        cache_age = time.time() - self._balance_cache_time

        if not force_refresh and self._balance_cache is not None and cache_age < 60:
            return self._balance_cache

        try:
            # Preferred path: delegate to the same method used by /wallet display
            if self._wallet_manager is not None and self._user_id is not None:
                balance = await self._wallet_manager._get_polymarket_balance(self._user_id)
                self._balance_cache = balance
                self._balance_cache_time = time.time()
                return balance

            # Fallback: direct CLOB call (used in tests / standalone usage)
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: self.client.update_balance_allowance(params)
            )
            balance_data = await loop.run_in_executor(
                None, lambda: self.client.get_balance_allowance(params)
            )
            balance_raw = float(balance_data.get('balance', 0))
            self._balance_cache = balance_raw / 1_000_000
            self._balance_cache_time = time.time()
            return self._balance_cache

        except Exception as e:
            logger.error(f"Error getting balance: {e}")
            return self._balance_cache or 0

    async def get_position(self, token_id: str) -> float:
        """Get position size for a specific token"""
        try:
            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id
            )
            loop = asyncio.get_running_loop()
            balance_data = await loop.run_in_executor(None, lambda: self.client.get_balance_allowance(params))
            return float(balance_data.get('balance', 0)) / 1_000_000

        except Exception as e:
            logger.debug(f"Error getting position: {e}")
            return 0

    async def get_buy_trade_size(self) -> float:
        """Calculate trade size for BUY orders based on available USDC cash"""
        # Percentage mode needs fresh balance — don't trust cache
        force_refresh = (self.settings.buy_amount_type == 'percentage')
        balance = await self.get_balance(force_refresh=force_refresh)

        logger.debug(f"Available USDC cash: ${balance:.2f}, mode: {self.settings.buy_amount_type}, value: {self.settings.buy_amount_value}")

        if balance < 1.0:
            logger.warning(f"USDC balance too low: ${balance:.2f}")
            return 0.0

        if self.settings.buy_amount_type == 'fixed':
            # Fixed USD amount, capped at balance
            trade_size = min(self.settings.buy_amount_value, balance)
        else:
            # Percentage of available USDC cash (not total portfolio)
            trade_size = balance * self.settings.buy_percentage_decimal

        # Apply legacy max_trade_size as a cap if it's set lower
        if self.settings.max_trade_size > 0:
            trade_size = min(trade_size, self.settings.max_trade_size)

        # Cap at balance minus small buffer to avoid exact-balance rejections
        trade_size = min(trade_size, balance)

        return max(1.0, trade_size) if trade_size >= 1.0 else 0.0

    async def get_sell_trade_size(self, token_id: str, price: float) -> float:
        """Calculate trade size for SELL orders based on settings and current holdings"""
        current_position = await self.get_position(token_id)

        if current_position < 0.01:
            return 0

        if self.settings.sell_amount_type == 'fixed':
            # Fixed USD amount - convert to shares
            shares_to_sell = self.settings.sell_amount_value / price if price > 0 else 0
            # Cap at current position
            shares_to_sell = min(shares_to_sell, current_position)
        else:
            # Percentage of holdings
            shares_to_sell = current_position * self.settings.sell_percentage_decimal
            # Cap at full position (rounding handled by order book)
            shares_to_sell = min(shares_to_sell, current_position)

        return max(0, shares_to_sell)

    async def should_copy_trade(self, trade: Dict) -> tuple[bool, str]:
        """Determine if a trade should be copied"""
        if not self.settings.enabled:
            return False, "Copy trading disabled"

        side = trade.get('side', 'BUY')
        price = float(trade.get('price', 0))
        usdc_size = float(trade.get('usdc_size', 0) or 0)

        if side == 'BUY' and not self.settings.copy_buys:
            return False, "Buy copying disabled"
        if side == 'SELL' and not self.settings.copy_sells:
            return False, "Sell copying disabled"

        if price < self.settings.min_price or price > self.settings.max_price:
            return False, f"Price (${price:.3f}) is outside range"

        if usdc_size < self.settings.min_target_trade_value:
            return False, f"Trade value ${usdc_size:.2f} below minimum"

        # Different validation for BUY vs SELL
        if side == 'BUY':
            # For buys, always fetch a fresh balance so recently-deposited USDC
            # is not blocked by a stale cached value.
            balance = await self.get_balance(force_refresh=True)
            if balance < 1:
                return False, f"Insufficient USDC balance (${balance:.2f})"

        elif side == 'SELL':
            # For sells, check if user has a position to sell
            token_id = trade.get('token_id') or trade.get('asset')

            # If we don't have token_id yet, we can't validate position here
            # Let it pass and validate later in copy_trade()
            if token_id:
                position = await self.get_position(token_id)
                if position < 0.01:
                    return False, f"No position to sell (holding {position:.2f} shares)"
            # If no token_id, we'll validate in copy_trade() method

        return True, "OK"

    async def copy_trade(self, trade: Dict, skip_validation: bool = False) -> Dict:
        """Copy a trade from a tracked wallet"""
        side = trade.get('side', 'BUY')
        price = float(trade.get('price', 0))
        size = float(trade.get('size', 0))
        usdc_size = float(trade.get('usdc_size', 0) or 0) or (size * price)
        market_name = trade.get('title', 'Unknown Market')
        outcome = trade.get('outcome', 'Unknown')
        token_id = trade.get('token_id') or trade.get('asset')
        condition_id = trade.get('condition_id')

        # Resolve token_id from condition_id once, upfront, so both
        # the sell-size calculation and the main execution block can use it.
        if not token_id and condition_id:
            try:
                loop = asyncio.get_running_loop()
                market = await loop.run_in_executor(None, lambda: self.client.get_market(condition_id))
                if market:
                    for token in market.get('tokens', []):
                        if token.get('outcome') == outcome:
                            token_id = token.get('token_id')
                            break
            except Exception as e:
                logger.debug(f"Could not resolve token_id from condition_id: {e}")

        if not skip_validation:
            should_copy, reason = await self.should_copy_trade(trade)
            if not should_copy:
                return {
                    'success': False,
                    'skipped': True,
                    'reason': reason,
                    'trade': trade
                }

        # Calculate trade size based on side
        if side == 'BUY':
            copy_usdc = await self.get_buy_trade_size()
            if copy_usdc < 1.0:
                return {
                    'success': False,
                    'skipped': True,
                    'reason': f'Calculated trade size too small (${copy_usdc:.2f}). Check USDC balance or increase percentage.',
                    'trade': trade
                }
            estimated_shares = copy_usdc / price if price > 0 else 0
        else:
            if token_id:
                shares_to_sell = await self.get_sell_trade_size(token_id, price)
                copy_usdc = shares_to_sell * price
                estimated_shares = shares_to_sell
            else:
                copy_usdc = 0
                estimated_shares = 0

        result = {
            'original_trade': {
                'market': market_name,
                'outcome': outcome,
                'side': side,
                'size': size,
                'price': price,
                'value': usdc_size
            },
            'copy_trade': {
                'side': side,
                'usdc_amount': copy_usdc,
                'estimated_shares': estimated_shares
            }
        }

        try:
            if not token_id:
                result['success'] = False
                result['error'] = 'Could not find token ID'
                return result

            # Validate liquidity before placing order
            liquidity_check = await self._check_order_liquidity(token_id, side, copy_usdc if side == 'BUY' else estimated_shares)
            if not liquidity_check['has_liquidity']:
                result['success'] = False
                result['error'] = f"Insufficient liquidity: {liquidity_check.get('reason', 'No matching orders')}"
                result['skipped'] = True
                logger.warning(f"Skipping trade due to insufficient liquidity: {liquidity_check.get('reason')}")
                return result

            if side == 'SELL':
                current_position = await self.get_position(token_id)
                if current_position < 0.01:
                    result['success'] = False
                    result['error'] = 'No position to sell'
                    return result

                shares_to_sell = await self.get_sell_trade_size(token_id, price)
                if shares_to_sell < 0.01:
                    result['success'] = False
                    result['error'] = 'Position too small to sell'
                    return result

                order_amount = shares_to_sell
                result['copy_trade']['estimated_shares'] = shares_to_sell
                result['copy_trade']['usdc_amount'] = shares_to_sell * price
            else:
                order_amount = copy_usdc

            try:
                loop = asyncio.get_running_loop()

                # Determine neg_risk from market data
                neg_risk = False
                if condition_id:
                    try:
                        market_info = await loop.run_in_executor(
                            None, lambda: self.client.get_market(condition_id)
                        )
                        neg_risk = market_info.get('neg_risk', False) if market_info else False
                    except Exception:
                        pass

                # Sync USDC allowance on this client instance so the CLOB
                # sees the current on-chain state before we post the order.
                try:
                    await loop.run_in_executor(
                        None,
                        lambda: self.client.update_balance_allowance(
                            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                        )
                    )
                except Exception as _sync_err:
                    logger.debug(f"Pre-order allowance sync failed (non-fatal): {_sync_err}")

                if side == 'BUY':
                    market_order_args = MarketOrderArgs(
                        token_id=token_id,
                        amount=order_amount,        # exact USD (e.g. $2.00)
                        side=BUY_SIDE,
                    )

                    signed_order = await loop.run_in_executor(
                        None,
                        lambda: self.client.create_market_order(market_order_args)
                    )

                    logger.debug(
                        f"Market BUY: ${order_amount:.2f} USD on {market_name[:30]} "
                    )

                    response = await loop.run_in_executor(
                        None,
                        lambda: self.client.post_order(signed_order, OrderType.FOK)
                    )

                else:
                    # SELL: amount is in shares, use limit order at best bid
                    book = await loop.run_in_executor(
                        None, lambda: self.client.get_order_book(token_id)
                    )
                    bids = getattr(book, 'bids', None) or (
                        book.get('bids', []) if isinstance(book, dict) else []
                    )

                    if not bids:
                        result['success'] = False
                        result['error'] = 'No bids in order book'
                        return result

                    def _parse_price(entry):
                        return float(
                            entry.get('price', 0) if isinstance(entry, dict)
                            else getattr(entry, 'price', 0)
                        )

                    best_bid = max(_parse_price(b) for b in bids)
                    best_bid = round(best_bid, 4)
                    sell_shares = math.floor(order_amount * 100) / 100

                    if sell_shares <= 0:
                        result['success'] = False
                        result['error'] = 'Order size too small after rounding'
                        return result

                    order_args = OrderArgs(
                        price=best_bid,
                        size=sell_shares,
                        side='SELL',
                        token_id=token_id,
                    )

                    signed_order = await loop.run_in_executor(
                        None,
                        lambda: self.client.create_order(
                            order_args,
                            PartialCreateOrderOptions(neg_risk=neg_risk)
                        )
                    )

                    logger.info(
                        f"Limit SELL: {sell_shares:.2f} shares @ ${best_bid:.4f} "
                        f"on {market_name[:30]}"
                    )

                    response = await loop.run_in_executor(
                        None,
                        lambda: self.client.post_order(signed_order, OrderType.GTC)
                    )

                # Handle response
                if response and response.get('success'):
                    result['success'] = True
                    result['order_id'] = response.get('orderID')
                    result['message'] = 'Trade executed!'
                    await self.get_balance(force_refresh=True)
                else:
                    result['success'] = False
                    result['error'] = f"Order failed: {response.get('error', 'Unknown error')}"

            except Exception as order_error:
                error_msg = str(order_error)
                error_lower = error_msg.lower()

                if 'no match' in error_lower:
                    result['success'] = False
                    result['error'] = 'Insufficient liquidity in order book'
                    result['skipped'] = True
                    logger.warning(f"Order failed due to liquidity: {market_name} - {outcome}")
                elif 'lower than the minimum' in error_lower:
                    result['success'] = False
                    result['error'] = f'Order below minimum: {error_msg}'
                    result['skipped'] = True
                    logger.warning(f"Order below minimum size: {error_msg}")
                elif 'allowance' in error_lower and 'allowance: 0' in error_msg:
                    # The CLOB confirmed allowance=0 on-chain — the Safe's approve()
                    # to the CTF Exchange contract was never executed or failed silently.
                    # The user must re-run "Complete Setup (Gasless)" from the bot UI.
                    result['success'] = False
                    result['error'] = (
                        'Safe wallet allowance not set on-chain (allowance=0). '
                        'Please tap ⚙️ Complete Setup in the trading wallet menu.'
                    )
                    result['skipped'] = True
                    logger.warning(
                        f"Allowance=0 for user {self._user_id} — on-chain approve() to "
                        f"CTF Exchange has not been executed. User must re-run Setup."
                    )
                    # Fire-and-forget: attempt to re-run setup automatically
                    if self._wallet_manager and self._user_id:
                        try:
                            loop2 = asyncio.get_running_loop()
                            loop2.create_task(
                                self._wallet_manager.setup_safe(self._user_id)
                            )
                            logger.info(f"Triggered automatic setup_safe for user {self._user_id}")
                        except Exception as _sync_err:
                            logger.debug(f"Could not schedule setup_safe: {_sync_err}")
                elif 'geoblock' in error_lower or '403' in error_msg or 'trading restricted' in error_lower:
                    result['success'] = False
                    result['error'] = 'Order rejected: trading restricted in this region (geoblock)'
                    result['skipped'] = True
                    logger.warning(f"Order geoblocked for {market_name} - {outcome}. The EC2 server's IP may be in a restricted region. Consider routing via a VPN/proxy.")
                else:
                    result['success'] = False
                    result['error'] = f"Order execution failed: {error_msg}"
                    logger.error(f"Order execution error: {order_error}", exc_info=True)


        except Exception as e:
            error_msg = str(e)
            result['success'] = False
            result['error'] = error_msg
            logger.error(f"Copy trade error: {e}", exc_info=True)

        return result



    async def _check_order_liquidity(self, token_id: str, side: str, amount: float) -> Dict:
        """
        Check if there's sufficient liquidity for an order.
        Returns: {'has_liquidity': bool, 'reason': str}
        """
        try:
            # Get order book for the token
            loop = asyncio.get_running_loop()
            book = await loop.run_in_executor(None, lambda: self.client.get_order_book(token_id))

            if not book:
                return {'has_liquidity': False, 'reason': 'Order book unavailable'}

            # For BUY orders, check asks (selling side)
            # For SELL orders, check bids (buying side)
            # OrderBookSummary is an object with .asks/.bids attributes, not a dict
            if side == 'BUY':
                orders = getattr(book, 'asks', None) or (book.get('asks', []) if isinstance(book, dict) else [])
            else:
                orders = getattr(book, 'bids', None) or (book.get('bids', []) if isinstance(book, dict) else [])

            if not orders or len(orders) == 0:
                return {'has_liquidity': False, 'reason': f'No {"asks" if side == "BUY" else "bids"} in order book'}

            # Calculate total available liquidity
            total_liquidity = 0
            for order in orders[:10]:  # Check top 10 orders
                order_size = float(order.get('size', 0) if isinstance(order, dict) else getattr(order, 'size', 0))
                total_liquidity += order_size

            # For BUY orders, amount is in USDC, need to convert to shares
            if side == 'BUY':
                # Use best ask price to estimate shares needed
                best_price = float(orders[0].get('price', 0) if isinstance(orders[0], dict) else getattr(orders[0], 'price', 0))
                if best_price == 0:
                    return {'has_liquidity': False, 'reason': 'Invalid order book price'}
                required_shares = amount / best_price
            else:
                required_shares = amount

            # Require the order size in available liquidity (1x = enough to fill the order)
            required_liquidity = required_shares

            if total_liquidity < required_liquidity:
                return {
                    'has_liquidity': False,
                    'reason': f'Insufficient liquidity (available: {total_liquidity:.2f}, required: {required_liquidity:.2f})'
                }

            # Also check minimum order size (Polymarket typically has ~$1 minimum)
            if side == 'BUY' and amount < 1.0:
                return {'has_liquidity': False, 'reason': 'Order below minimum size ($1)'}

            return {'has_liquidity': True}

        except Exception as e:
            error_str = str(e).lower()
            if '404' in error_str or 'no orderbook' in error_str:
                return {'has_liquidity': False, 'reason': 'Market is closed or no longer active'}
            # Other errors: fail safe
            logger.warning(f"Error checking liquidity: {e}")
            return {'has_liquidity': False, 'reason': f'Error checking liquidity: {e}'}

class CopyTradeManager:
    """Manages copy trading for all users"""

    def __init__(self, db, wallet_manager, cache_manager=None):
        self.db = db
        self.wallet_manager = wallet_manager
        self.cache_manager = cache_manager
        self._traders: Dict[int, CopyTrader] = {}
        self._trader_created_at: Dict[int, float] = {} 

    async def get_user_settings(self, user_id: int) -> CopyTradeSettings:
        data = await self.db.get_copy_trade_settings(user_id)
        if data:
            return CopyTradeSettings.from_dict(data)
        return CopyTradeSettings()


    def clear_trader_cache(self, user_id: int):
        """Remove cached trader and its timestamp together."""
        self._traders.pop(user_id, None)
        self._trader_created_at.pop(user_id, None)

    async def save_user_settings(self, user_id: int, settings: CopyTradeSettings) -> bool:
        return await self.db.save_copy_trade_settings(user_id, settings.to_dict())

    async def update_setting(self, user_id: int, setting_name: str, value) -> bool:
        """Update a single setting"""
        settings = await self.get_user_settings(user_id)

        if hasattr(settings, setting_name):
            setattr(settings, setting_name, value)
            success = await self.save_user_settings(user_id, settings)
            if success:
                self.clear_trader_cache(user_id)
            return success
        return False

    async def get_trader(self, user_id: int) -> Optional[CopyTrader]:
        """Get or create CopyTrader for a user, with 10-minute TTL."""
        if user_id in self._traders:
            age = time.time() - self._trader_created_at.get(user_id, 0)
            if age < 600:
                return self._traders[user_id]
            else:
                logger.debug(f"Trader cache expired for user {user_id}, rebuilding...")
                self.clear_trader_cache(user_id)

        # Build a fully-configured Privy-backed ClobClient
        clob_client = await self.wallet_manager._get_clob_client(user_id)
        if not clob_client:
            logger.warning(f"Could not create CLOB client for user {user_id}")
            return None

        wallet = await self.wallet_manager.get_wallet(user_id)
        safe_address = wallet.get('safe_address') if wallet else None

        if not safe_address:
            logger.warning(f"No Safe address for user {user_id}")
            return None

        settings = await self.get_user_settings(user_id)

        trader = CopyTrader(
            clob_client=clob_client,
            safe_address=safe_address,
            settings=settings,
            wallet_manager=self.wallet_manager,
            user_id=user_id,
        )

        self._traders[user_id] = trader
        self._trader_created_at[user_id] = time.time()
        return trader

    async def process_trade_for_copiers(
            self,
            trade: Dict,
            source_wallet: str,
            context
        ) -> List[Dict]:
        """Process a trade and copy it for eligible users (excluding multi_buy_only users)"""
        results = []

        logger.debug(f"\U0001f4b4 Processing trade for copiers from wallet: {source_wallet[:10]}...")
        logger.debug(f"   Trade: {trade.get('side')} {trade.get('title', 'Unknown')[:30]} @ ${trade.get('price', 0)}")

        users = await self.db.get_users_with_copy_trading(source_wallet)
        logger.debug(f"   Found {len(users)} users with copy trading enabled for this wallet")

        for user_data in users:
            user_id = user_data['user_id']

            # Skip users with multi_buy_only enabled - they're handled separately
            settings = await self.get_user_settings(user_id)
            if settings.multi_buy_only:
                logger.debug(f"   Skipping user {user_id} (multi_buy_only mode)")
                continue

            logger.debug(f"   Processing copy for user {user_id}")

            # Redis dedup lock: prevent duplicate processing of the same trade for this user
            lock_key = f"copy_trade_lock:{user_id}:{trade.get('transaction_hash', '')}"
            lock = None
            if self.cache_manager:
                lock = await self.cache_manager.acquire_lock(lock_key, timeout=30)
                if not lock:
                    logger.debug(f"Trade already being processed for user {user_id}")
                    continue

            try:
                trader = await self.get_trader(user_id)
                if not trader:
                    logger.warning(f"   <! Could not create trader for user {user_id}")
                    continue

                logger.info(f"   \u2705 Trader created, checking if should copy...")

                should_copy, reason = await trader.should_copy_trade(trade)
                logger.info(f"   Should copy: {should_copy}, Reason: {reason}")

                if not should_copy:
                    results.append({
                        'user_id': user_id,
                        'success': False,
                        'skipped': True,
                        'reason': reason
                    })
                    continue

                logger.info(f"   Executing copy trade...")
                result = await trader.copy_trade(trade)
                result['user_id'] = user_id
                results.append(result)

                logger.debug(f"   Copy result: success={result.get('success')}, error={result.get('error', 'None')}")

                await self.db.log_copy_trade(
                    user_id=user_id,
                    source_wallet=source_wallet,
                    original_trade=trade,
                    copy_result=result
                )

            except Exception as e:
                logger.error(f"   <! Copy trade error for user {user_id}: {e}", exc_info=True)
                results.append({
                    'user_id': user_id,
                    'success': False,
                    'error': str(e)
                })
            finally:
                if lock:
                    try:
                        await lock.release()
                    except Exception:
                        pass

        executed = sum(1 for r in results if r.get('success'))
        skipped  = sum(1 for r in results if r.get('skipped'))
        failed   = sum(1 for r in results if not r.get('success') and not r.get('skipped'))

        if executed > 0:
            logger.debug(f"   < Done: {executed} executed, {skipped} skipped, {failed} failed")
        else:
            logger.debug(f"   Done: {executed} executed, {skipped} skipped, {failed} failed")
        return results

    async def process_multibuy_copy_trades(
            self,
            trade: Dict,
            wallet_addresses: List[str],
            context
        ) -> List[Dict]:
        """Process copy trades for users with multi_buy_only enabled."""
        results = []

        wallet_count = len(wallet_addresses)

        users = await self.db.get_users_with_multibuy_copy_trading(wallet_addresses)
        logger.debug(f"   Found {len(users)} users with multi-buy-only copy trading")

        # Fresh price fetch (your existing code — with the CHAIN_ID fix applied)
        token_id = trade.get('token_id') or trade.get('asset')
        trade = {**trade}  # local copy so we don't mutate caller's dict

        condition_id = trade.get('condition_id')
        outcome_str = trade.get('outcome', '')
        if condition_id and outcome_str:
            try:
                temp_resolver = ClobClient(CopyTrader.HOST, key="", chain_id=CopyTrader.CHAIN_ID)
                market_data = temp_resolver.get_market(condition_id)
                if market_data:
                    for token in market_data.get('tokens', []):
                        if token.get('outcome', '').upper() == outcome_str.upper():
                            token_id = token.get('token_id')
                            logger.debug(f"Resolved token_id for '{outcome_str}': {token_id[:10]}...")
                            break
            except Exception as e:
                logger.debug(f"Could not resolve token_id from condition_id: {e}")

        try:
            if token_id:
                from py_clob_client.client import ClobClient
                temp_client = ClobClient(CopyTrader.HOST, key="", chain_id=CopyTrader.CHAIN_ID)
                book = temp_client.get_order_book(token_id)
                asks = getattr(book, 'asks', None) or (book.get('asks', []) if isinstance(book, dict) else [])
                if book and asks:
                    prices = []
                    for o in asks[:50]:
                        p = float(o.get('price', 0) if isinstance(o, dict) else getattr(o, 'price', 0))
                        s = float(o.get('size', 0) if isinstance(o, dict) else getattr(o, 'size', 0))
                        if p > 0 and s > 0:
                            prices.append(p)
                    if prices:
                        trade['price'] = min(prices)   # best ask
                else:
                    logger.warning(f"   No asks in order book for {token_id}. Skipping all copies.")
                    return []
        except Exception as e:
            error_str = str(e).lower()
            if '404' in error_str or 'no orderbook' in error_str:
                logger.warning(f"   Market has no orderbook (404). Aborting multi-buy copies.")
                return []
            logger.warning(f"   Could not fetch fresh market data: {e}. Using original price.")


        for user_data in users:
            user_id = user_data['user_id']
            logger.debug(f"   Processing multi-buy copy for user {user_id}")

            try:
                trader = await self.get_trader(user_id)
                if not trader:
                    logger.warning(f"   <! Could not create trader for user {user_id}")
                    continue

                # Re-check wallet count with this user's own time window setting.
                # The outer detection always uses hours=1 to decide IF a multibuy
                # occurred, but each user may have configured a different window
                # (e.g. 4h, 8h).  We re-query the DB here so the count reflects
                # their personal window.
                user_window = trader.settings.multibuywindow
                user_threshold = trader.settings.multibuythreshold
                market_id_for_query = (
                    trade.get('condition_id')
                    or trade.get('market_slug')
                    or trade.get('market_id')
                )
                outcome_for_query = trade.get('outcome', '')
                if market_id_for_query and outcome_for_query and user_window != 1:
                    window_wallets = await self.db.get_multibuy_wallets(
                        market_id_for_query, outcome_for_query, hours=user_window
                    )
                    effective_wallet_count = len(window_wallets)
                    logger.debug(
                        f"   User {user_id}: window={user_window}h → "
                        f"{effective_wallet_count} wallets (detection found {wallet_count})"
                    )
                else:
                    effective_wallet_count = wallet_count

                if effective_wallet_count < user_threshold:
                    logger.debug(
                        f"   Skipping user {user_id}: only {effective_wallet_count} wallets "
                        f"in {user_window}h window (threshold: {user_threshold})"
                    )
                    results.append({
                        'user_id': user_id,
                        'success': False,
                        'skipped': True,
                        'reason': (
                            f"Only {effective_wallet_count} wallets bought in your "
                            f"{user_window}h window (threshold: {user_threshold})"
                        ),
                        'is_multibuy': True
                    })
                    continue

                # enforce multibuysellmode if it's set to 'all'
                if trader.settings.multibuysellmode == 'all':
                    users_tracked_wallets = await self.db.get_tracked_wallets(user_id)
                    tracked_set = {w['address'] for w in users_tracked_wallets}
                    buying_set = set(wallet_addresses)
                    if not buying_set.issuperset(tracked_set):
                        missing = len(tracked_set - buying_set)
                        logger.debug(f"   Skipping user {user_id}: 'all' mode, {missing} tracked wallets haven't bought")
                        results.append({
                            'user_id': user_id,
                            'success': False,
                            'skipped': True,
                            'reason': f"Not all tracked wallets have bought yet (mode: all)",
                            'is_multibuy': True
                        })
                        continue

                should_copy, reason = await trader.should_copy_trade(trade)

                # Override: multi-buy signal overrides the minimum trade value filter
                if not should_copy and 'below minimum' in reason.lower():
                    should_copy = True
                    reason = "Multi-buy signal overrides minimum trade value"

                if not should_copy:
                    results.append({
                        'user_id': user_id,
                        'success': False,
                        'skipped': True,
                        'reason': reason,
                        'is_multibuy': True
                    })
                    continue

                result = await trader.copy_trade(trade)
                result['user_id'] = user_id
                result['is_multibuy'] = True
                result['wallet_count'] = wallet_count
                results.append(result)

                if result.get('success'):
                    logger.info(f"   ✅ Multi-buy copy succeeded for user {user_id}")
                elif result.get('skipped'):
                    logger.info(f"   ⏭️ Multi-buy copy skipped for user {user_id}: {result.get('reason')}")
                else:
                    logger.warning(f"   ❌ Multi-buy copy failed for user {user_id}: {result.get('error')}")

                await self.db.log_copy_trade(
                    user_id=user_id,
                    source_wallet=','.join(wallet_addresses[:3]),
                    original_trade={**trade, 'multibuy_wallets': wallet_count},
                    copy_result=result
                )

            except Exception as e:
                logger.error(f"   Multi-buy copy error for user {user_id}: {e}", exc_info=True)
                results.append({
                    'user_id': user_id,
                    'success': False,
                    'error': str(e),
                    'is_multibuy': True
                })

        return results
