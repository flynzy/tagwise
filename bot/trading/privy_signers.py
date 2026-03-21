# bot/trading/privy_signers.py
"""
Custom signer classes and order builder that delegate signing to Privy
instead of using raw private keys.

Replaces:
- py_clob_client.signer.Signer  → PrivyClobSigner
- py_clob_client.order_builder.builder.OrderBuilder → PrivyOrderBuilder
- py_builder_relayer_client.signer.Signer → PrivyRelayerSigner
- create_level_1_headers() → create_privy_level_1_headers()
"""

import asyncio
import logging
from datetime import datetime

from py_order_utils.model import OrderData, SignedOrder, BUY as UtilsBuy, SELL as UtilsSell
from py_order_utils.model.order import Order
from py_order_utils.model.signatures import EOA, POLY_GNOSIS_SAFE
from py_order_utils.utils import generate_seed, normalize_address, prepend_zx
from py_clob_client.config import get_contract_config
from py_clob_client.order_builder.builder import ROUNDING_CONFIG
from py_clob_client.order_builder.helpers import (
    to_token_decimals,
    round_down,
    round_normal,
    decimal_places,
    round_up,
)
from py_clob_client.order_builder.constants import BUY, SELL
from py_clob_client.clob_types import OrderArgs, CreateOrderOptions, MarketOrderArgs

from poly_eip712_structs import make_domain
from eth_utils import keccak
from hexbytes import HexBytes
from eth_account.messages import encode_defunct

logger = logging.getLogger(__name__)


# ─── EIP-712 Domain Helpers ───────────────────────────────────────────────

def _order_domain(chain_id: int, exchange_address: str) -> dict:
    """Build EIP-712 domain dict for Order signing (Privy typed_data format)."""
    return {
        "name": "Polymarket CTF Exchange",
        "version": "1",
        "chainId": str(chain_id),
        "verifyingContract": exchange_address,
    }


ORDER_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Order": [
        {"name": "salt", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "signer", "type": "address"},
        {"name": "taker", "type": "address"},
        {"name": "tokenId", "type": "uint256"},
        {"name": "makerAmount", "type": "uint256"},
        {"name": "takerAmount", "type": "uint256"},
        {"name": "expiration", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "feeRateBps", "type": "uint256"},
        {"name": "side", "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
    ],
}


CLOB_AUTH_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
    ],
    "ClobAuth": [
        {"name": "address", "type": "address"},
        {"name": "timestamp", "type": "string"},
        {"name": "nonce", "type": "uint256"},
        {"name": "message", "type": "string"},
    ],
}


# ─── PrivyClobSigner ──────────────────────────────────────────────────────

class PrivyClobSigner:
    """
    Drop-in replacement for py_clob_client.signer.Signer.
    Does NOT hold a private key — delegates signing to Privy.

    Only `address()` and `get_chain_id()` are used by ClobClient normally.
    The `sign()` path is bypassed via PrivyOrderBuilder.
    """

    def __init__(self, privy_service, wallet_id: str, address: str, chain_id: int = 137):
        self.privy_service = privy_service
        self.wallet_id = wallet_id
        self._address = address
        self.chain_id = chain_id

    def address(self) -> str:
        return self._address

    def get_chain_id(self) -> int:
        return self.chain_id

    @property
    def private_key(self):
        raise AttributeError(
            "PrivyClobSigner does not hold a private key. "
            "Use PrivyOrderBuilder for signing."
        )

    def sign(self, message_hash):
        raise NotImplementedError(
            "PrivyClobSigner.sign() is not supported. "
            "Order signing is handled by PrivyOrderBuilder via Privy API."
        )


# ─── PrivyOrderBuilder ───────────────────────────────────────────────────

class PrivyOrderBuilder:
    """
    Replaces py_clob_client.order_builder.builder.OrderBuilder.
    Builds the same OrderData/amounts but signs via Privy's eth_signTypedData_v4
    instead of using a local UtilsSigner with a raw private key.
    """

    def __init__(self, privy_service, wallet_id: str, address: str,
                 chain_id: int = 137, sig_type=None, funder=None):
        self.privy_service = privy_service
        self.wallet_id = wallet_id
        self._address = address
        self.chain_id = chain_id
        self.sig_type = sig_type if sig_type is not None else POLY_GNOSIS_SAFE
        self.funder = funder if funder is not None else address

        # Mimic the interface expected by ClobClient
        self.signer = PrivyClobSigner(privy_service, wallet_id, address, chain_id)

    def _get_order_amounts(self, side, size, price, round_config):
        """Reuse exact amount calculation from SDK."""
        raw_price = round_normal(price, round_config.price)

        if side == BUY:
            raw_taker_amt = round_down(size, round_config.size)
            raw_maker_amt = raw_taker_amt * raw_price
            if decimal_places(raw_maker_amt) > round_config.amount:
                raw_maker_amt = round_up(raw_maker_amt, round_config.amount + 4)
                if decimal_places(raw_maker_amt) > round_config.amount:
                    raw_maker_amt = round_down(raw_maker_amt, round_config.amount)
            return UtilsBuy, to_token_decimals(raw_maker_amt), to_token_decimals(raw_taker_amt)

        elif side == SELL:
            raw_maker_amt = round_down(size, round_config.size)
            raw_taker_amt = raw_maker_amt * raw_price
            if decimal_places(raw_taker_amt) > round_config.amount:
                raw_taker_amt = round_up(raw_taker_amt, round_config.amount + 4)
                if decimal_places(raw_taker_amt) > round_config.amount:
                    raw_taker_amt = round_down(raw_taker_amt, round_config.amount)
            return UtilsSell, to_token_decimals(raw_maker_amt), to_token_decimals(raw_taker_amt)

        raise ValueError(f"side must be '{BUY}' or '{SELL}'")

    def _get_market_order_amounts(self, side, amount, price, round_config):
        """Reuse exact market order amount calculation from SDK."""
        raw_price = round_normal(price, round_config.price)

        if side == BUY:
            raw_maker_amt = round_down(amount, round_config.size)
            raw_taker_amt = raw_maker_amt / raw_price
            if decimal_places(raw_taker_amt) > round_config.amount:
                raw_taker_amt = round_up(raw_taker_amt, round_config.amount + 4)
                if decimal_places(raw_taker_amt) > round_config.amount:
                    raw_taker_amt = round_down(raw_taker_amt, round_config.amount)
            return UtilsBuy, to_token_decimals(raw_maker_amt), to_token_decimals(raw_taker_amt)

        elif side == SELL:
            raw_maker_amt = round_down(amount, round_config.size)
            raw_taker_amt = raw_maker_amt * raw_price
            if decimal_places(raw_taker_amt) > round_config.amount:
                raw_taker_amt = round_up(raw_taker_amt, round_config.amount + 4)
                if decimal_places(raw_taker_amt) > round_config.amount:
                    raw_taker_amt = round_down(raw_taker_amt, round_config.amount)
            return UtilsSell, to_token_decimals(raw_maker_amt), to_token_decimals(raw_taker_amt)

        raise ValueError(f"side must be '{BUY}' or '{SELL}'")

    def _build_order_data(self, side_int, maker_amount, taker_amount,
                          token_id, taker, fee_rate_bps, nonce, expiration):
        """Build OrderData matching SDK format."""
        return OrderData(
            maker=self.funder,
            taker=taker,
            tokenId=token_id,
            makerAmount=str(maker_amount),
            takerAmount=str(taker_amount),
            side=side_int,
            feeRateBps=str(fee_rate_bps),
            nonce=str(nonce),
            signer=self._address,
            expiration=str(expiration),
            signatureType=self.sig_type,
        )

    def _build_order_struct(self, data: OrderData) -> Order:
        """Build the EIP-712 Order struct (same logic as py_order_utils)."""
        salt = int(generate_seed())

        if data.signer is None:
            data.signer = data.maker

        return Order(
            salt=salt,
            maker=normalize_address(data.maker),
            signer=normalize_address(data.signer),
            taker=normalize_address(data.taker),
            tokenId=int(data.tokenId),
            makerAmount=int(data.makerAmount),
            takerAmount=int(data.takerAmount),
            expiration=int(data.expiration),
            nonce=int(data.nonce),
            feeRateBps=int(data.feeRateBps),
            side=int(data.side),
            signatureType=int(data.signatureType),
        )

    def _build_typed_data_for_order(self, order: Order, exchange_address: str) -> dict:
        """Build full EIP-712 typed data JSON for Privy signing."""
        return {
            "types": ORDER_TYPES,
            "primary_type": "Order",
            "domain": _order_domain(self.chain_id, exchange_address),
            "message": {
                "salt": str(order["salt"]),
                "maker": order["maker"],
                "signer": order["signer"],
                "taker": order["taker"],
                "tokenId": str(order["tokenId"]),
                "makerAmount": str(order["makerAmount"]),
                "takerAmount": str(order["takerAmount"]),
                "expiration": str(order["expiration"]),
                "nonce": str(order["nonce"]),
                "feeRateBps": str(order["feeRateBps"]),
                "side": order["side"],
                "signatureType": order["signatureType"],
            },
        }

    async def _sign_order_via_privy(self, data: OrderData, neg_risk: bool) -> SignedOrder:
        """Build Order struct, create typed data, sign via Privy."""
        order = self._build_order_struct(data)

        contract_config = get_contract_config(self.chain_id, neg_risk)
        typed_data = self._build_typed_data_for_order(order, contract_config.exchange)

        signature = await self.privy_service.sign_typed_data(self.wallet_id, typed_data)
        return SignedOrder(order, signature)

    def create_order(self, order_args: OrderArgs, options: CreateOrderOptions) -> SignedOrder:
        """Create and sign a limit order via Privy (sync wrapper for async)."""
        side, maker_amount, taker_amount = self._get_order_amounts(
            order_args.side, order_args.size, order_args.price,
            ROUNDING_CONFIG[options.tick_size],
        )

        data = self._build_order_data(
            side, maker_amount, taker_amount,
            order_args.token_id, order_args.taker,
            order_args.fee_rate_bps, order_args.nonce,
            order_args.expiration,
        )

        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self._sign_order_via_privy(data, options.neg_risk))
                return future.result()
        else:
            return asyncio.run(self._sign_order_via_privy(data, options.neg_risk))

    def create_market_order(self, order_args: MarketOrderArgs, options: CreateOrderOptions) -> SignedOrder:
        """Create and sign a market order via Privy (sync wrapper for async)."""
        side, maker_amount, taker_amount = self._get_market_order_amounts(
            order_args.side, order_args.amount, order_args.price,
            ROUNDING_CONFIG[options.tick_size],
        )

        data = self._build_order_data(
            side, maker_amount, taker_amount,
            order_args.token_id, order_args.taker,
            order_args.fee_rate_bps, order_args.nonce,
            "0",  # market orders have no expiration
        )

        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self._sign_order_via_privy(data, options.neg_risk))
                return future.result()
        else:
            return asyncio.run(self._sign_order_via_privy(data, options.neg_risk))

    # Expose calculate methods from the original builder (used by ClobClient for market orders)
    def calculate_buy_market_price(self, positions, amount_to_match, order_type):
        from py_clob_client.order_builder.builder import OrderBuilder as OrigBuilder
        return OrigBuilder.calculate_buy_market_price(self, positions, amount_to_match, order_type)

    def calculate_sell_market_price(self, positions, amount_to_match, order_type):
        from py_clob_client.order_builder.builder import OrderBuilder as OrigBuilder
        return OrigBuilder.calculate_sell_market_price(self, positions, amount_to_match, order_type)


# ─── CLOB Auth Signing (L1 Headers) ──────────────────────────────────────

async def privy_sign_clob_auth(privy_service, wallet_id: str,
                                address: str, chain_id: int = 137,
                                timestamp: int = None, nonce: int = 0) -> str:
    """
    Sign a ClobAuth message via Privy's eth_signTypedData_v4.
    Returns the signature hex string.
    """
    if timestamp is None:
        timestamp = int(datetime.now().timestamp())

    typed_data = {
        "types": CLOB_AUTH_TYPES,
        "primary_type": "ClobAuth",
        "domain": {
            "name": "ClobAuthDomain",
            "version": "1",
            "chainId": str(chain_id),
        },
        "message": {
            "address": address,
            "timestamp": str(timestamp),
            "nonce": nonce,
            "message": "This message attests that I control the given wallet",
        },
    }

    return await privy_service.sign_typed_data(wallet_id, typed_data)


async def create_privy_level_1_headers(privy_service, wallet_id: str,
                                        address: str, chain_id: int = 137,
                                        nonce: int = 0) -> dict:
    """
    Create L1 authentication headers using Privy signing.
    Replaces create_level_1_headers() from py_clob_client.
    """
    timestamp = int(datetime.now().timestamp())
    signature = await privy_sign_clob_auth(
        privy_service, wallet_id, address, chain_id, timestamp, nonce
    )

    return {
        "POLY_ADDRESS": address,
        "POLY_SIGNATURE": signature,
        "POLY_TIMESTAMP": str(timestamp),
        "POLY_NONCE": str(nonce),
    }


# ─── PrivyRelayerSigner ──────────────────────────────────────────────────

class PrivyRelayerSigner:
    """
    Drop-in replacement for py_builder_relayer_client.signer.Signer.
    Delegates signing to Privy instead of using a local private key.

    The relayer calls:
    - signer.address() → returns stored address
    - signer.sign_eip712_struct_hash(struct_hash) → signs with EIP-191 prefix via Privy
    """

    def __init__(self, privy_service, wallet_id: str, address: str, chain_id: int = 137, safe_factory: str = None):
        self.privy_service = privy_service
        self.wallet_id = wallet_id
        self._address = address
        self.chain_id = chain_id
        self.safe_factory = safe_factory

    def address(self) -> str:
        return self._address

    def get_chain_id(self) -> int:
        return self.chain_id

    def sign(self, message_hash) -> str:
        """
        Sign the Safe create deployment payload using eth_signTypedData_v4.
        Reconstructs the CreateProxy typed data so Privy computes the same
        final hash as poly_eip712_structs.generate_struct_hash(), then signs
        it with raw ECDSA — matching Signer.sign() exactly.
        """
        ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "CreateProxy": [
                    {"name": "paymentToken", "type": "address"},
                    {"name": "payment", "type": "uint256"},
                    {"name": "paymentReceiver", "type": "address"},
                ],
            },
            "domain": {
                "name": "Polymarket Contract Proxy Factory",
                "chainId": self.chain_id,
                "verifyingContract": self.safe_factory,
            },
            "primary_type": "CreateProxy",
            "message": {
                "paymentToken": ZERO_ADDRESS,
                "payment": 0,
                "paymentReceiver": ZERO_ADDRESS,
            },
        }

        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self.privy_service.sign_typed_data(self.wallet_id, typed_data)
                )
                return future.result()
        else:
            return asyncio.run(
                self.privy_service.sign_typed_data(self.wallet_id, typed_data)
            )



    def sign_eip712_struct_hash(self, struct_hash) -> str:

        if isinstance(struct_hash, bytes):
            hex_msg = "0x" + struct_hash.hex()
        elif isinstance(struct_hash, str):
            hex_msg = struct_hash if struct_hash.startswith("0x") else "0x" + struct_hash
        else:
            hex_msg = "0x" + bytes(struct_hash).hex()

        loop = asyncio.get_event_loop()

        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self.privy_service.personal_sign(self.wallet_id, hex_msg)
                )
                result = future.result()
                return result
        else:
            result = asyncio.run(
                self.privy_service.personal_sign(self.wallet_id, hex_msg)
            )
            return result
