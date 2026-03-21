# tests/test_privy_service.py
"""
Unit tests for PrivyService — mocks the Privy API to verify our wrapper logic.
Run: .venv/bin/python3 -m pytest tests/test_privy_service.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_fake_user(user_id="did:privy:abc123", wallet_id="wlt_123", wallet_address="0xABCD"):
    wallet_account = MagicMock()
    wallet_account.type = "wallet"
    wallet_account.id = wallet_id
    wallet_account.address = wallet_address

    telegram_account = MagicMock()
    telegram_account.type = "telegram"

    user = MagicMock()
    user.id = user_id
    user.linked_accounts = [telegram_account, wallet_account]
    user.created_at = 1700000000000
    return user


def _make_fake_rpc_response(signature="0xdeadbeef"):
    data = MagicMock()
    data.signature = signature
    data.encoding = "hex"
    resp = MagicMock()
    resp.data = data
    resp.method = "eth_signTypedData_v4"
    return resp


def _make_service_with_mock_client():
    """Create a PrivyService and directly replace its .client with a mock."""
    # Patch the import so constructor doesn't fail
    with patch("privy.AsyncPrivyAPI") as MockAPI:
        mock_client = AsyncMock()
        MockAPI.return_value = mock_client

        from bot.trading.privy_service import PrivyService
        svc = PrivyService(app_id="test", app_secret="test")

    # Now replace the client directly
    svc.client = mock_client
    return svc, mock_client


# ── Tests ────────────────────────────────────────────────────────────────

class TestPrivyServiceCreateUser:

    @pytest.mark.asyncio
    async def test_create_user_with_wallet_success(self):
        svc, mock_client = _make_service_with_mock_client()

        fake_user = _make_fake_user(
            user_id="did:privy:test1",
            wallet_id="wlt_abc",
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        mock_client.users.create = AsyncMock(return_value=fake_user)

        result = await svc.create_user_with_wallet(telegram_user_id=99999)

        assert result["privy_user_id"] == "did:privy:test1"
        assert result["privy_wallet_id"] == "wlt_abc"
        assert result["wallet_address"] == "0x1234567890abcdef1234567890abcdef12345678"

        mock_client.users.create.assert_called_once()
        call_kwargs = mock_client.users.create.call_args.kwargs
        assert call_kwargs["linked_accounts"][0]["type"] == "telegram"
        assert call_kwargs["linked_accounts"][0]["telegram_user_id"] == "99999"
        assert call_kwargs["create_ethereum_wallet"] is True

    @pytest.mark.asyncio
    async def test_create_user_no_wallet_raises(self):
        svc, mock_client = _make_service_with_mock_client()

        telegram_account = MagicMock()
        telegram_account.type = "telegram"
        user = MagicMock()
        user.id = "did:privy:nowallet"
        user.linked_accounts = [telegram_account]
        mock_client.users.create = AsyncMock(return_value=user)

        with pytest.raises(RuntimeError, match="no embedded wallet"):
            await svc.create_user_with_wallet(telegram_user_id=12345)


class TestPrivyServiceSigning:

    @pytest.mark.asyncio
    async def test_sign_typed_data(self):
        svc, mock_client = _make_service_with_mock_client()
        mock_client.wallets.rpc = AsyncMock(
            return_value=_make_fake_rpc_response("abcdef1234")
        )

        sig = await svc.sign_typed_data("wlt_123", {"types": {}, "domain": {}, "message": {}})

        assert sig == "0xabcdef1234"
        mock_client.wallets.rpc.assert_called_once_with(
            wallet_id="wlt_123",
            method="eth_signTypedData_v4",
            params={"typed_data": {"types": {}, "domain": {}, "message": {}}},
        )

    @pytest.mark.asyncio
    async def test_sign_typed_data_already_prefixed(self):
        svc, mock_client = _make_service_with_mock_client()
        mock_client.wallets.rpc = AsyncMock(
            return_value=_make_fake_rpc_response("0xdeadbeef")
        )

        sig = await svc.sign_typed_data("wlt_123", {})
        assert sig == "0xdeadbeef"
        assert not sig.startswith("0x0x")

    @pytest.mark.asyncio
    async def test_personal_sign(self):
        svc, mock_client = _make_service_with_mock_client()
        resp = _make_fake_rpc_response("0xsignature123")
        resp.method = "personal_sign"
        mock_client.wallets.rpc = AsyncMock(return_value=resp)

        sig = await svc.personal_sign("wlt_456", "0xdeadbeef")

        assert sig == "0xsignature123"
        mock_client.wallets.rpc.assert_called_once_with(
            wallet_id="wlt_456",
            method="personal_sign",
            params={"message": "deadbeef", "encoding": "hex"},
        )

    @pytest.mark.asyncio
    async def test_personal_sign_strips_0x(self):
        svc, mock_client = _make_service_with_mock_client()
        mock_client.wallets.rpc = AsyncMock(
            return_value=_make_fake_rpc_response("0xabc")
        )

        await svc.personal_sign("wlt_1", "0xCAFE")

        call_params = mock_client.wallets.rpc.call_args.kwargs["params"]
        assert call_params["message"] == "CAFE"


class TestPrivyServiceGetWallet:

    @pytest.mark.asyncio
    async def test_get_wallet_address(self):
        svc, mock_client = _make_service_with_mock_client()
        wallet = MagicMock()
        wallet.address = "0x9999"
        mock_client.wallets.get = AsyncMock(return_value=wallet)

        addr = await svc.get_wallet_address("wlt_xyz")
        assert addr == "0x9999"

    @pytest.mark.asyncio
    async def test_get_wallet_address_error(self):
        svc, mock_client = _make_service_with_mock_client()
        mock_client.wallets.get = AsyncMock(side_effect=Exception("not found"))

        addr = await svc.get_wallet_address("wlt_bad")
        assert addr is None
