# bot/trading/privy_service.py
import os
import asyncio
import logging
import re
from typing import Optional, Dict

logger = logging.getLogger(__name__)


def _normalize_privy_auth_key(key: str) -> str:
    """
    Prepare a Privy authorization key for use with update_authorization_key().

    The Privy Python SDK's update_authorization_key() handles the
    "wallet-auth:<base64>" format natively — it does its own internal
    conversion. DO NOT pre-convert to PEM: if we wrap the payload in
    PEM headers first, the SDK will double-process the key and corrupt it,
    causing InvalidByte errors during request signing.

    Rules:
    - wallet-auth:<base64>  →  pass through as-is (SDK handles it)
    - PEM with literal \\n  →  normalise \\n to real newlines
    - Strip BOM / null bytes that can sneak in via .env editors
    """
    key = key.strip()

    # Strip BOM, null bytes, and bare carriage returns
    key = key.replace("\x00", "").replace("\ufeff", "").replace("\r", "")

    if key.startswith("wallet-auth:"):
        # Strip any stray whitespace/newlines that .env parsing might add
        # to the payload — keep the wallet-auth: prefix exactly as-is.
        b64 = re.sub(r"[^A-Za-z0-9+/=]", "", key[len("wallet-auth:"):])
        return "wallet-auth:" + b64

    # Already PEM — normalise escaped newlines
    return key.replace("\\n", "\n")


class PrivyService:
    """Wraps Privy's PrivyAPI for user/wallet creation and signing."""

    def __init__(self, app_id: str, app_secret: str, authorization_key: str = None):
        self._app_id = app_id
        self._app_secret = app_secret
        # Normalise once at construction; stored for reuse in _make_client()
        self._normalized_key: str | None = (
            _normalize_privy_auth_key(authorization_key) if authorization_key else None
        )
        if not self._normalized_key:
            logger.warning("PRIVY_AUTH_KEY is not set — Privy signing will not work!")
        # Long-lived client for non-signing admin operations (users.create, wallets.create, wallets.list)
        self.client = self._make_client()

    def _make_client(self):
        """
        Return a fresh PrivyAPI (httpx.Client) instance.

        The Privy SDK's httpx.Client is NOT thread-safe — it manages connection
        pools and request-signing state that gets corrupted when two threads call
        it simultaneously.  By creating a fresh client per signing call we
        eliminate all shared-state races.
        """
        from privy import PrivyAPI
        client = PrivyAPI(app_id=self._app_id, app_secret=self._app_secret)
        if self._normalized_key:
            client.update_authorization_key(self._normalized_key)
        return client

    async def _run_in_thread(self, fn, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def create_user_with_wallet(self, telegram_user_id: int) -> Dict:
        # 1) Create user (sync call, run in thread)
        user = await self._run_in_thread(
            self.client.users.create,
            linked_accounts=[{
                "type": "telegram",
                "telegram_user_id": str(telegram_user_id),
            }],
        )

        # 2) Create wallet owned by your quorum
        quorum_id = os.getenv("PRIVY_QUORUM_ID")
        wallet = await self._run_in_thread(
            self.client.wallets.create,
            chain_type="ethereum",
            owner_id=quorum_id,
        )

        logger.info(
            f"Created Privy user {user.id} with wallet {wallet.address} "
            f"for Telegram user {telegram_user_id}"
        )

        return {
            "privy_user_id": user.id,
            "privy_wallet_id": wallet.id,
            "wallet_address": wallet.address,
        }

    async def sign_typed_data(self, wallet_id: str, typed_data: dict) -> str:
        # Use a fresh client per call — the Privy SDK's httpx.Client is not
        # thread-safe and corrupts its internal state under concurrent signing.
        def _do():
            client = self._make_client()
            response = client.wallets.rpc(
                wallet_id=wallet_id,
                method="eth_signTypedData_v4",
                params={"typed_data": typed_data},
            )
            sig = response.data.signature
            return ("0x" + sig) if not sig.startswith("0x") else sig

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do)

    async def personal_sign(self, wallet_id: str, message_hex: str) -> str:
        msg = message_hex[2:] if message_hex.startswith("0x") else message_hex

        def _do():
            client = self._make_client()
            response = client.wallets.rpc(
                wallet_id=wallet_id,
                method="personal_sign",
                params={"message": msg, "encoding": "hex"},
            )
            sig = response.data.signature
            return ("0x" + sig) if not sig.startswith("0x") else sig

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do)
