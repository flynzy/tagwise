# bot/trading/privy_service.py
import os
import asyncio
import logging
import textwrap
from typing import Optional, Dict

logger = logging.getLogger(__name__)


def _normalize_privy_auth_key(key: str) -> str:
    """
    Convert a Privy authorization key to PEM format.

    Privy authorization keys are distributed in two formats:

    1. Raw PKCS#8 DER bytes base64-encoded, with a "wallet-auth:" prefix:
           wallet-auth:MIGHAgEAMBMGByqGSM49...

       This is NOT a valid PEM file. The cryptography library's
       load_pem_private_key() will fail with InvalidByte because it
       encounters raw DER bytes (0x81, 0x30, etc.) that are not valid
       base64-inside-PEM.

       Fix: strip the prefix, wrap in PEM headers/footers.

    2. Standard PEM (already has -----BEGIN PRIVATE KEY----- header).
       May have literal \\n instead of real newlines (common in .env files).

       Fix: normalise \\n → real newlines and return as-is.
    """
    key = key.strip()

    if key.startswith("wallet-auth:"):
        # Extract the raw base64 payload after the prefix
        b64 = key[len("wallet-auth:"):]
        # Strip any accidental whitespace / URL-encoded characters
        b64 = b64.strip().replace("\n", "").replace("\r", "").replace(" ", "")
        # Wrap at 64 chars per line (PEM line length convention)
        wrapped = "\n".join(textwrap.wrap(b64, 64))
        return f"-----BEGIN PRIVATE KEY-----\n{wrapped}\n-----END PRIVATE KEY-----\n"

    # Already PEM-ish — just normalise escaped newlines
    return key.replace("\\n", "\n")


class PrivyService:
    """Wraps Privy's PrivyAPI for user/wallet creation and signing."""

    def __init__(self, app_id: str, app_secret: str, authorization_key: str = None):
        from privy import PrivyAPI  # <-- sync client, NOT AsyncPrivyAPI

        self.client = PrivyAPI(
            app_id=app_id,
            app_secret=app_secret,
        )
        if authorization_key:
            self.client.update_authorization_key(
                _normalize_privy_auth_key(authorization_key)
            )

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

        # Debug: inspect all wallet attributes
        print(f"DEBUG wallet object: {wallet}")
        print(f"DEBUG wallet.__dict__: {vars(wallet) if hasattr(wallet, '__dict__') else dir(wallet)}")
        print(f"DEBUG wallet.id: {getattr(wallet, 'id', 'MISSING')}")
        print(f"DEBUG wallet.address: {getattr(wallet, 'address', 'MISSING')}")

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
        response = await self._run_in_thread(
            self.client.wallets.rpc,
            wallet_id=wallet_id,
            method="eth_signTypedData_v4",
            params={"typed_data": typed_data},
        )
        sig = response.data.signature
        if not sig.startswith("0x"):
            sig = "0x" + sig
        return sig

    async def personal_sign(self, wallet_id: str, message_hex: str) -> str:
        msg = message_hex[2:] if message_hex.startswith("0x") else message_hex
        response = await self._run_in_thread(
            self.client.wallets.rpc,
            wallet_id=wallet_id,
            method="personal_sign",
            params={"message": msg, "encoding": "hex"},
        )
        sig = response.data.signature
        if not sig.startswith("0x"):
            sig = "0x" + sig
        return sig
