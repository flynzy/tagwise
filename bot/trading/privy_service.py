# bot/trading/privy_service.py
import os
import asyncio
import logging
import textwrap
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
        import re as _re
        prefix = "wallet-auth:"
        b64 = key[len(prefix):]
        # Remove any non-base64 chars from the payload only (preserves prefix)
        b64 = _re.sub(r"[^A-Za-z0-9+/=]", "", b64)
        return prefix + b64

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
        if self._normalized_key:
            try:
                pem_bytes = self._normalized_key.encode("utf-8")
                null_count = pem_bytes.count(0)
                lines = self._normalized_key.splitlines()
                logger.info(
                    f"PRIVY_AUTH_KEY startup check: "
                    f"lines={len(lines)}, "
                    f"total_len={len(self._normalized_key)}, "
                    f"null_bytes={null_count}, "
                    f"first_line={repr(lines[0] if lines else 'EMPTY')}, "
                    f"last_line={repr(lines[-1] if lines else 'EMPTY')}"
                )
                with open("/tmp/privy_key_debug.pem", "w", encoding="utf-8") as f:
                    f.write(self._normalized_key)
                # Write a quick verify script
                with open("/tmp/verify_privy_key.py", "w", encoding="utf-8") as f:
                    f.write(
                        "from cryptography.hazmat.primitives.serialization import load_pem_private_key\n"
                        "data = open('/tmp/privy_key_debug.pem','rb').read()\n"
                        "print('len=',len(data),'null_bytes=',data.count(0))\n"
                        "print('start=',repr(data[:60]))\n"
                        "print('end=  ',repr(data[-40:]))\n"
                        "try:\n"
                        "    k=load_pem_private_key(data,password=None)\n"
                        "    print('[OK]',type(k).__name__)\n"
                        "except Exception as e:\n"
                        "    print('[FAIL]',e)\n"
                    )
                logger.info("Wrote /tmp/privy_key_debug.pem and /tmp/verify_privy_key.py")
            except Exception as _e:
                logger.warning(f"Could not write PRIVY_AUTH_KEY debug files: {_e}")
        else:
            logger.warning("PRIVY_AUTH_KEY is None — authorization key not set!")
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
            # Diagnostic: log key metadata so we can detect corruption at runtime
            key_bytes = self._normalized_key.encode("utf-8")
            null_positions = [i for i, b in enumerate(key_bytes) if b == 0]
            if null_positions:
                logger.error(
                    f"PRIVY_AUTH_KEY has null bytes at positions {null_positions[:5]} "
                    f"(key len={len(self._normalized_key)}, bytes len={len(key_bytes)}). "
                    f"First 60 chars: {repr(self._normalized_key[:60])}"
                )
            else:
                logger.debug(
                    f"PRIVY_AUTH_KEY normalized OK: len={len(self._normalized_key)}, "
                    f"starts={repr(self._normalized_key[:40])}, "
                    f"ends={repr(self._normalized_key[-20:])}"
                )
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
