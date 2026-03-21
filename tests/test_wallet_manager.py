# tests/test_wallet_manager.py
"""
Unit tests for WalletManager with Privy integration.
Mocks both Privy and the database to test wallet creation/setup logic.

Run: pytest tests/test_wallet_manager.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


def _mock_db():
    """Create a mock database with common methods."""
    db = AsyncMock()
    db.get_user_wallet = AsyncMock(return_value=None)
    db.save_user_wallet = AsyncMock(return_value=True)
    db.get_user_api_creds = AsyncMock(return_value=None)
    db.save_user_api_creds = AsyncMock(return_value=True)
    db.delete_user_wallet = AsyncMock(return_value=True)
    db.delete_user_api_creds = AsyncMock(return_value=True)
    db.update_wallet_safe_address = AsyncMock(return_value=True)
    db.update_wallet_allowances_set = AsyncMock(return_value=True)
    return db


def _mock_privy_service():
    """Create a mock PrivyService."""
    svc = AsyncMock()
    svc.create_user_with_wallet = AsyncMock(return_value={
        "privy_user_id": "did:privy:test123",
        "privy_wallet_id": "wlt_abc",
        "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
    })
    svc.sign_typed_data = AsyncMock(return_value="0xfakesig")
    svc.personal_sign = AsyncMock(return_value="0xfakesig")
    return svc


def _mock_builder():
    """Create a mock BuilderRelayer."""
    builder = MagicMock()
    builder.derive_safe_address = MagicMock(
        return_value="0xsafe_address_derived_from_eoa"
    )
    builder.get_safe_status = MagicMock(return_value={
        "safe_address": "0xsafe_address_derived_from_eoa",
        "deployed": True,
        "allowances_set": True,
    })
    builder.deploy_safe_privy = MagicMock(return_value={"success": True})
    builder.set_allowances_privy = MagicMock(return_value={"success": True})
    builder.transfer_usdc_to_safe_privy = MagicMock(return_value={"success": True})
    builder.withdraw_from_safe_privy = MagicMock(return_value={"success": True})
    return builder


class TestWalletManagerCreate:

    @pytest.mark.asyncio
    async def test_create_wallet_success(self):
        """Should create a Privy wallet and save to DB without a private key."""
        db = _mock_db()
        privy_svc = _mock_privy_service()

        with patch("bot.trading.wallet_manager.get_builder_relayer") as mock_gbr, \
             patch("bot.trading.wallet_manager._get_privy_service", return_value=privy_svc):
            mock_gbr.return_value = _mock_builder()

            from bot.trading.wallet_manager import WalletManager
            wm = WalletManager(db)
            wm._privy_service = privy_svc

            result = await wm.create_wallet(user_id=12345)

            assert result["success"] is True
            assert result["address"] == "0x1234567890abcdef1234567890abcdef12345678"
            assert "private_key" not in result  # Privy wallets don't expose keys

            # Verify DB was called correctly
            db.save_user_wallet.assert_called_once()
            call_kwargs = db.save_user_wallet.call_args.kwargs
            assert call_kwargs["privy_user_id"] == "did:privy:test123"
            assert call_kwargs["privy_wallet_id"] == "wlt_abc"
            assert call_kwargs["encrypted_private_key"] is None  # No key stored!
            assert call_kwargs["wallet_type"] == "privy"

    @pytest.mark.asyncio
    async def test_create_wallet_existing_returns_error(self):
        """Should reject if user already has a wallet."""
        db = _mock_db()
        db.get_user_wallet = AsyncMock(return_value={
            "address": "0xexisting",
            "safe_address": "0xsafe",
        })

        with patch("bot.trading.wallet_manager.get_builder_relayer") as mock_gbr, \
             patch("bot.trading.wallet_manager._get_privy_service"):
            mock_gbr.return_value = _mock_builder()

            from bot.trading.wallet_manager import WalletManager
            wm = WalletManager(db)

            result = await wm.create_wallet(user_id=12345)
            assert result["success"] is False
            assert "already have a wallet" in result["error"]


class TestWalletManagerSetupSafe:

    @pytest.mark.asyncio
    async def test_setup_safe_calls_privy_methods(self):
        """Should call deploy_safe_privy and set_allowances_privy."""
        db = _mock_db()
        db.get_user_wallet = AsyncMock(return_value={
            "address": "0xeoa",
            "safe_address": "0xsafe",
            "privy_wallet_id": "wlt_abc",
            "privy_user_id": "did:privy:123",
        })
        privy_svc = _mock_privy_service()

        builder = _mock_builder()
        # Safe not yet deployed
        builder.get_safe_status = MagicMock(side_effect=[
            {"safe_address": "0xsafe", "deployed": False, "allowances_set": False},
            {"safe_address": "0xsafe", "deployed": True, "allowances_set": True},
        ])

        with patch("bot.trading.wallet_manager.get_builder_relayer", return_value=builder), \
             patch("bot.trading.wallet_manager._get_privy_service", return_value=privy_svc), \
             patch.object(
                 # Mock _activate_trading to avoid the full CLOB client flow
                 __import__("bot.trading.wallet_manager", fromlist=["WalletManager"]).WalletManager,
                 "_activate_trading",
                 new_callable=lambda: lambda self, uid: AsyncMock(return_value={"success": True}),
             ):
            from bot.trading.wallet_manager import WalletManager
            wm = WalletManager(db)
            wm._privy_service = privy_svc

            # Patch _activate_trading directly on the instance
            wm._activate_trading = AsyncMock(return_value={"success": True})

            result = await wm.setup_safe(user_id=12345)

            assert result["success"] is True
            builder.deploy_safe_privy.assert_called_once_with(
                privy_svc, "wlt_abc", "0xeoa"
            )
            builder.set_allowances_privy.assert_called_once_with(
                privy_svc, "wlt_abc", "0xeoa", "0xsafe"
            )

    @pytest.mark.asyncio
    async def test_setup_safe_no_privy_wallet(self):
        """Should fail if no privy_wallet_id in DB."""
        db = _mock_db()
        db.get_user_wallet = AsyncMock(return_value={
            "address": "0xeoa",
            "safe_address": "0xsafe",
            "privy_wallet_id": None,
            "privy_user_id": None,
        })

        with patch("bot.trading.wallet_manager.get_builder_relayer", return_value=_mock_builder()), \
             patch("bot.trading.wallet_manager._get_privy_service"):
            from bot.trading.wallet_manager import WalletManager
            wm = WalletManager(db)

            result = await wm.setup_safe(user_id=12345)
            assert result["success"] is False
            assert "No Privy wallet" in result["error"]


class TestWalletManagerClobClient:

    @pytest.mark.asyncio
    async def test_get_clob_client_patches_signer_and_builder(self):
        """The CLOB client should have PrivyClobSigner and PrivyOrderBuilder."""
        db = _mock_db()
        db.get_user_wallet = AsyncMock(return_value={
            "address": "0xeoa_addr",
            "safe_address": "0xsafe_addr",
            "proxy_address": None,
            "privy_wallet_id": "wlt_test",
            "privy_user_id": "did:privy:test",
        })
        db.get_user_api_creds = AsyncMock(return_value={
            "api_key": "key",
            "api_secret": "secret",
            "api_passphrase": "pass",
        })

        privy_svc = _mock_privy_service()

        with patch("bot.trading.wallet_manager.get_builder_relayer", return_value=_mock_builder()), \
             patch("bot.trading.wallet_manager._get_privy_service", return_value=privy_svc):
            from bot.trading.wallet_manager import WalletManager
            wm = WalletManager(db)
            wm._privy_service = privy_svc

            client = await wm._get_clob_client(user_id=99999)

            assert client is not None

            from bot.trading.privy_signers import PrivyClobSigner, PrivyOrderBuilder
            assert isinstance(client.signer, PrivyClobSigner)
            assert isinstance(client.builder, PrivyOrderBuilder)
            assert client.signer.address() == "0xeoa_addr"
            assert client.builder.funder == "0xsafe_addr"

    @pytest.mark.asyncio
    async def test_get_clob_client_no_privy_wallet_returns_none(self):
        """Should return None if no privy_wallet_id."""
        db = _mock_db()
        db.get_user_wallet = AsyncMock(return_value={
            "address": "0xeoa",
            "safe_address": "0xsafe",
            "privy_wallet_id": None,
        })

        with patch("bot.trading.wallet_manager.get_builder_relayer", return_value=_mock_builder()), \
             patch("bot.trading.wallet_manager._get_privy_service"):
            from bot.trading.wallet_manager import WalletManager
            wm = WalletManager(db)

            client = await wm._get_clob_client(user_id=99999)
            assert client is None


class TestWalletManagerDepositsWithdrawals:

    @pytest.mark.asyncio
    async def test_deposit_calls_privy_method(self):
        """deposit_to_safe should use transfer_usdc_to_safe_privy."""
        db = _mock_db()
        db.get_user_wallet = AsyncMock(return_value={
            "address": "0xeoa",
            "safe_address": "0xsafe",
            "privy_wallet_id": "wlt_1",
        })
        privy_svc = _mock_privy_service()
        builder = _mock_builder()

        with patch("bot.trading.wallet_manager.get_builder_relayer", return_value=builder), \
             patch("bot.trading.wallet_manager._get_privy_service", return_value=privy_svc):
            from bot.trading.wallet_manager import WalletManager
            wm = WalletManager(db)
            wm._privy_service = privy_svc

            result = await wm.deposit_to_safe(user_id=1, amount=10.0)

            assert result["success"] is True
            builder.transfer_usdc_to_safe_privy.assert_called_once_with(
                privy_svc, "wlt_1", "0xeoa", "0xsafe", 10.0
            )

    @pytest.mark.asyncio
    async def test_withdraw_calls_privy_method(self):
        """withdraw_usdc should use withdraw_from_safe_privy."""
        db = _mock_db()
        db.get_user_wallet = AsyncMock(return_value={
            "address": "0xeoa",
            "safe_address": "0xsafe",
            "privy_wallet_id": "wlt_1",
        })
        privy_svc = _mock_privy_service()
        builder = _mock_builder()

        with patch("bot.trading.wallet_manager.get_builder_relayer", return_value=builder), \
             patch("bot.trading.wallet_manager._get_privy_service", return_value=privy_svc):
            from bot.trading.wallet_manager import WalletManager
            wm = WalletManager(db)
            wm._privy_service = privy_svc

            # Mock get_balances to return enough balance
            wm.get_balances = AsyncMock(return_value={
                "success": True,
                "safe_usdc": 100.0,
                "polymarket_usdc": 0.0,
            })

            result = await wm.withdraw_usdc(
                user_id=1,
                to_address="0x" + "a" * 40,
                amount=50.0,
            )

            assert result["success"] is True
            builder.withdraw_from_safe_privy.assert_called_once()
