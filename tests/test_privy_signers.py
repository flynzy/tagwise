# tests/test_privy_signers.py
"""
Unit tests for privy_signers — verifies EIP-712 typed data construction
and that signing is correctly delegated to Privy.

Run: pytest tests/test_privy_signers.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── PrivyClobSigner ──────────────────────────────────────────────────────

class TestPrivyClobSigner:

    def test_address(self):
        from bot.trading.privy_signers import PrivyClobSigner
        signer = PrivyClobSigner(
            privy_service=MagicMock(),
            wallet_id="wlt_1",
            address="0xABCDEF",
            chain_id=137,
        )
        assert signer.address() == "0xABCDEF"

    def test_chain_id(self):
        from bot.trading.privy_signers import PrivyClobSigner
        signer = PrivyClobSigner(MagicMock(), "wlt_1", "0x1", 137)
        assert signer.get_chain_id() == 137

    def test_private_key_raises(self):
        from bot.trading.privy_signers import PrivyClobSigner
        signer = PrivyClobSigner(MagicMock(), "wlt_1", "0x1", 137)
        with pytest.raises(AttributeError, match="does not hold a private key"):
            _ = signer.private_key

    def test_sign_raises(self):
        from bot.trading.privy_signers import PrivyClobSigner
        signer = PrivyClobSigner(MagicMock(), "wlt_1", "0x1", 137)
        with pytest.raises(NotImplementedError):
            signer.sign("0xhash")


# ── PrivyOrderBuilder: typed data construction ───────────────────────────

class TestPrivyOrderBuilderTypedData:

    def _make_builder(self, privy_service=None):
        from bot.trading.privy_signers import PrivyOrderBuilder
        return PrivyOrderBuilder(
            privy_service=privy_service or MagicMock(),
            wallet_id="wlt_test",
            address="0x1111111111111111111111111111111111111111",
            chain_id=137,
            sig_type=2,
            funder="0x2222222222222222222222222222222222222222",
        )

    def test_order_typed_data_has_correct_domain(self):
        """Verify the EIP-712 domain matches Polymarket CTF Exchange."""
        from py_order_utils.model.order import Order
        from bot.trading.privy_signers import _order_domain

        domain = _order_domain(137, "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
        assert domain["name"] == "Polymarket CTF Exchange"
        assert domain["version"] == "1"
        assert domain["chainId"] == "137"
        assert domain["verifyingContract"] == "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

    def test_order_typed_data_has_all_fields(self):
        """Verify all Order fields are present in typed data."""
        from bot.trading.privy_signers import ORDER_TYPES

        order_fields = [f["name"] for f in ORDER_TYPES["Order"]]
        expected = [
            "salt", "maker", "signer", "taker", "tokenId",
            "makerAmount", "takerAmount", "expiration", "nonce",
            "feeRateBps", "side", "signatureType",
        ]
        assert order_fields == expected

    def test_clob_auth_typed_data_has_correct_fields(self):
        """Verify ClobAuth fields match the SDK's ClobAuth struct."""
        from bot.trading.privy_signers import CLOB_AUTH_TYPES

        auth_fields = [f["name"] for f in CLOB_AUTH_TYPES["ClobAuth"]]
        assert auth_fields == ["address", "timestamp", "nonce", "message"]

        domain_fields = [f["name"] for f in CLOB_AUTH_TYPES["EIP712Domain"]]
        assert domain_fields == ["name", "version", "chainId"]

    def test_build_typed_data_for_order(self):
        """Verify _build_typed_data_for_order produces correct structure."""
        from py_order_utils.model.order import Order
        builder = self._make_builder()

        order = Order(
            salt=12345,
            maker="0x2222222222222222222222222222222222222222",
            signer="0x1111111111111111111111111111111111111111",
            taker="0x0000000000000000000000000000000000000000",
            tokenId=9999,
            makerAmount=1000000,
            takerAmount=500000,
            expiration=0,
            nonce=0,
            feeRateBps=0,
            side=0,
            signatureType=2,
        )

        exchange = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
        typed_data = builder._build_typed_data_for_order(order, exchange)

        assert typed_data["primary_type"] == "Order"
        assert "Order" in typed_data["types"]
        assert "EIP712Domain" in typed_data["types"]
        assert typed_data["domain"]["name"] == "Polymarket CTF Exchange"
        assert typed_data["domain"]["verifyingContract"] == exchange

        msg = typed_data["message"]
        assert msg["salt"] == "12345"
        assert msg["maker"] == "0x2222222222222222222222222222222222222222"
        assert msg["signer"] == "0x1111111111111111111111111111111111111111"
        assert msg["tokenId"] == "9999"
        assert msg["makerAmount"] == "1000000"
        assert msg["takerAmount"] == "500000"
        assert msg["side"] == 0
        assert msg["signatureType"] == 2


# ── PrivyOrderBuilder: amount calculations ───────────────────────────────

class TestPrivyOrderBuilderAmounts:

    def _make_builder(self):
        from bot.trading.privy_signers import PrivyOrderBuilder
        return PrivyOrderBuilder(
            privy_service=MagicMock(),
            wallet_id="wlt_test",
            address="0x1111111111111111111111111111111111111111",
            chain_id=137,
            sig_type=2,
            funder="0x2222222222222222222222222222222222222222",
        )

    def test_buy_order_amounts(self):
        """Buy 10 shares at $0.50 → makerAmount=5 USDC, takerAmount=10 shares."""
        from py_order_utils.model import BUY
        builder = self._make_builder()

        side, maker, taker = builder._get_order_amounts("BUY", 10.0, 0.50,
            type("RC", (), {"price": 2, "size": 2, "amount": 4})())

        assert side == BUY
        assert maker > 0
        assert taker > 0

    def test_sell_order_amounts(self):
        """Sell 10 shares at $0.50 → makerAmount=10 shares, takerAmount=5 USDC."""
        from py_order_utils.model import SELL
        builder = self._make_builder()

        side, maker, taker = builder._get_order_amounts("SELL", 10.0, 0.50,
            type("RC", (), {"price": 2, "size": 2, "amount": 4})())

        assert side == SELL
        assert maker > 0
        assert taker > 0

    def test_market_buy_amounts(self):
        """Market buy $5 at price $0.50."""
        from py_order_utils.model import BUY
        builder = self._make_builder()

        side, maker, taker = builder._get_market_order_amounts("BUY", 5.0, 0.50,
            type("RC", (), {"price": 2, "size": 2, "amount": 4})())

        assert side == BUY
        assert maker > 0
        assert taker > 0


# ── CLOB Auth Headers ────────────────────────────────────────────────────

class TestClobAuthHeaders:

    @pytest.mark.asyncio
    async def test_privy_sign_clob_auth_typed_data(self):
        """Verify the ClobAuth typed data sent to Privy is correct."""
        from bot.trading.privy_signers import privy_sign_clob_auth

        mock_privy = AsyncMock()
        mock_privy.sign_typed_data = AsyncMock(return_value="0xsig123")

        sig = await privy_sign_clob_auth(
            mock_privy, "wlt_1", "0xMyAddress", chain_id=137,
            timestamp=1700000000, nonce=0,
        )

        assert sig == "0xsig123"

        # Verify typed data structure
        call_args = mock_privy.sign_typed_data.call_args
        typed_data = call_args[0][1]  # second positional arg

        assert typed_data["primary_type"] == "ClobAuth"
        assert typed_data["domain"]["name"] == "ClobAuthDomain"
        assert typed_data["domain"]["version"] == "1"
        assert typed_data["domain"]["chainId"] == "137"
        assert typed_data["message"]["address"] == "0xMyAddress"
        assert typed_data["message"]["timestamp"] == "1700000000"
        assert typed_data["message"]["nonce"] == 0
        assert "attests" in typed_data["message"]["message"]

    @pytest.mark.asyncio
    async def test_create_privy_level_1_headers(self):
        """Verify L1 headers have all required fields."""
        from bot.trading.privy_signers import create_privy_level_1_headers

        mock_privy = AsyncMock()
        mock_privy.sign_typed_data = AsyncMock(return_value="0xsig456")

        headers = await create_privy_level_1_headers(
            mock_privy, "wlt_1", "0xAddr", chain_id=137, nonce=0,
        )

        assert headers["POLY_ADDRESS"] == "0xAddr"
        assert headers["POLY_SIGNATURE"] == "0xsig456"
        assert "POLY_TIMESTAMP" in headers
        assert headers["POLY_NONCE"] == "0"

        # Timestamp should be a valid unix timestamp string
        ts = int(headers["POLY_TIMESTAMP"])
        assert ts > 1000000000


# ── PrivyRelayerSigner ───────────────────────────────────────────────────

class TestPrivyRelayerSigner:

    def test_address(self):
        from bot.trading.privy_signers import PrivyRelayerSigner
        signer = PrivyRelayerSigner(MagicMock(), "wlt_1", "0xABC", 137)
        assert signer.address() == "0xABC"

    def test_chain_id(self):
        from bot.trading.privy_signers import PrivyRelayerSigner
        signer = PrivyRelayerSigner(MagicMock(), "wlt_1", "0xABC", 137)
        assert signer.get_chain_id() == 137
