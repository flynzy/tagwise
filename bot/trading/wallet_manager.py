# bot/trading/wallet_manager.py
"""
Wallet management for Polymarket trading with gasless Safe wallets.
Keys are managed by Privy (TEE-backed) — our server never touches raw keys.
"""

import os
import logging
from typing import Optional, Dict
from web3 import Web3
import asyncio

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

from bot.config import Config
from bot.trading.builder_relayer import get_builder_relayer
from bot.trading.privy_service import PrivyService
from bot.trading.privy_signers import (
    PrivyClobSigner,
    PrivyOrderBuilder,
    PrivyRelayerSigner,
    create_privy_level_1_headers,
)

logger = logging.getLogger(__name__)

# Contract addresses
POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
# Native USDC on Polygon (used by Polymarket)
USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
# Legacy USDC.e (PoS bridged) - kept for reference
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Polymarket CLOB API
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


def _get_privy_service() -> PrivyService:
    """Create a PrivyService instance from config."""
    app_id = Config.PRIVY_APP_ID
    app_secret = Config.PRIVY_APP_SECRET
    auth_key = Config.PRIVY_AUTH_KEY

    if not app_id or not app_secret:
        raise ValueError(
            "PRIVY_APP_ID and PRIVY_APP_SECRET must be set. "
            "Create an app at https://privy.io and set env vars."
        )

    return PrivyService(
        app_id=app_id,
        app_secret=app_secret,
        authorization_key=auth_key,
    )

import requests

def _fetch_positions(safe_address: str) -> list:
    """Fetch open positions from Polymarket data API."""
    try:
        url = f"https://data-api.polymarket.com/positions?user={safe_address}&sizeThreshold=.01"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Error fetching positions from data API: {e}")
        return []



# Module-level lock: _get_clob_client patches a module-global function
# (create_level_1_headers) to inject Privy signing. If two coroutines
# call _get_clob_client concurrently they race on that global — one
# restores the original while the other is still using the patched version.
# This lock serialises all calls so the patch/restore is atomic per call.
_clob_client_lock: "asyncio.Lock | None" = None

def _get_clob_lock() -> "asyncio.Lock":
    """Return (lazily creating) the module-level asyncio.Lock."""
    global _clob_client_lock
    if _clob_client_lock is None:
        import asyncio as _asyncio
        _clob_client_lock = _asyncio.Lock()
    return _clob_client_lock


class WalletManager:
    """Manages user trading wallets with gasless Safe architecture + Privy TEE keys"""

    def __init__(self, db):
        self.db = db
        self.w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

        # Add PoA middleware
        try:
            from web3.middleware import ExtraDataToPOAMiddleware
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except ImportError:
            from web3.middleware import geth_poa_middleware
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        # Builder Relayer for gasless operations
        try:
            self.builder = get_builder_relayer()
            self.gasless_enabled = True
        except ValueError as e:
            logger.warning(f"Builder Relayer not configured: {e}")
            self.builder = None
            self.gasless_enabled = False

        # Privy service (lazy init — may not be configured yet)
        self._privy_service = None

    @property
    def privy_service(self) -> PrivyService:
        if self._privy_service is None:
            self._privy_service = _get_privy_service()
        return self._privy_service

    async def _get_clob_client(self, user_id: int) -> Optional[ClobClient]:
        """Create an authenticated CLOB client for a user (Privy-backed signing)."""
        wallet = await self.get_wallet(user_id)
        if not wallet:
            return None

        privy_wallet_id = wallet.get('privy_wallet_id')
        if not privy_wallet_id:
            logger.warning(f"No Privy wallet ID for user {user_id}")
            return None

        safe_address = wallet.get('safe_address') or wallet.get('proxy_address')
        if not safe_address:
            logger.warning(f"No Safe address for user {user_id}")
            return None

        eoa_address = wallet['address']

        try:
            # Create ClobClient with a dummy key to pass constructor validation.
            # We immediately replace the signer and builder with Privy-backed versions.
            dummy_key = "0x" + "0" * 64
            client = ClobClient(
                host=CLOB_HOST,
                key=dummy_key,
                chain_id=CHAIN_ID,
                signature_type=2,  # Always Safe/proxy
                funder=safe_address,
            )

            # Replace signer with Privy-backed signer
            privy_signer = PrivyClobSigner(
                self.privy_service, privy_wallet_id, eoa_address, CHAIN_ID
            )
            client.signer = privy_signer

            # Replace order builder with Privy-backed builder
            client.builder = PrivyOrderBuilder(
                privy_service=self.privy_service,
                wallet_id=privy_wallet_id,
                address=eoa_address,
                chain_id=CHAIN_ID,
                sig_type=2,  # POLY_GNOSIS_SAFE
                funder=safe_address,
            )

            # Serialise the module-level patch/restore so concurrent callers
            # don't trample each other's create_level_1_headers replacement.
            import py_clob_client.client as clob_client_mod
            lock = _get_clob_lock()

            async def _derive_l1_headers():
                return await create_privy_level_1_headers(
                    self.privy_service, privy_wallet_id, eoa_address, CHAIN_ID
                )

            def privy_create_level_1_headers(signer, nonce=None):
                # signer is ignored — we use Privy instead
                import asyncio
                import concurrent.futures

                loop = asyncio.get_event_loop()
                if loop.is_running():
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        future = pool.submit(asyncio.run, _derive_l1_headers())
                        return future.result()
                else:
                    return asyncio.run(_derive_l1_headers())

            # --- BEGIN critical section ---
            async with lock:
                original_create_level_1 = clob_client_mod.create_level_1_headers
                clob_client_mod.create_level_1_headers = privy_create_level_1_headers
                try:
                    api_creds = await self.db.get_user_api_creds(user_id, signature_type=2)
                    if api_creds:
                        from py_clob_client.clob_types import ApiCreds
                        creds = ApiCreds(
                            api_key=api_creds['api_key'],
                            api_secret=api_creds['api_secret'],
                            api_passphrase=api_creds['api_passphrase'],
                        )
                        client.set_api_creds(creds)
                    else:
                        creds = client.create_or_derive_api_creds()
                        client.set_api_creds(creds)
                        await self.db.save_user_api_creds(user_id, {
                            'api_key': creds.api_key,
                            'api_secret': creds.api_secret,
                            'api_passphrase': creds.api_passphrase,
                            'signature_type': 2,
                        })
                        logger.info(f"Derived new API creds for user {user_id}")
                finally:
                    # Always restore original so other callers aren't affected
                    clob_client_mod.create_level_1_headers = original_create_level_1
            # --- END critical section ---

            return client

        except Exception as e:
            logger.error(f"Failed to create CLOB client: {e}", exc_info=True)
            return None

    async def create_wallet(self, user_id: int) -> Dict:
        """Create a new wallet via Privy (TEE-backed) with Safe for gasless trading."""
        try:
            existing = await self.get_wallet(user_id)
            if existing:
                return {
                    'success': False,
                    'error': 'You already have a wallet. Use /wallet to view it.',
                    'address': existing.get('address'),
                }

            # Create user + wallet via Privy
            privy_result = await self.privy_service.create_user_with_wallet(user_id)
            eoa_address = privy_result['wallet_address']
            privy_user_id = privy_result['privy_user_id']
            privy_wallet_id = privy_result['privy_wallet_id']

            # Derive Safe address
            safe_address = None
            if self.builder:
                safe_address = self.builder.derive_safe_address(eoa_address)

            # Save to DB (NO private key — it lives in Privy's TEE)
            success = await self.db.save_user_wallet(
                user_id=user_id,
                address=eoa_address.lower(),
                encrypted_private_key=None,
                safe_address=safe_address,
                wallet_type='privy',
                privy_user_id=privy_user_id,
                privy_wallet_id=privy_wallet_id,
            )

            if success:
                logger.info(
                    f"Created Privy wallet for user {user_id}: "
                    f"EOA={eoa_address[:10]}, Safe={safe_address[:10] if safe_address else 'N/A'}"
                )
                return {
                    'success': True,
                    'address': eoa_address,
                    'safe_address': safe_address,
                    'message': 'Wallet created successfully!',
                }
            else:
                return {'success': False, 'error': 'Failed to save wallet.'}

        except Exception as e:
            logger.error(f"Error creating wallet: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    async def setup_safe(self, user_id: int) -> Dict:
            """
            Robust: Deploy Safe, set dual allowances, and force-sync CLOB.
            Wraps synchronous builder calls in threads to prevent event-loop clashes.
            """
            if not self.builder:
                return {'success': False, 'error': 'Builder Relayer not configured'}

            wallet = await self.get_wallet(user_id)
            if not wallet:
                return {'success': False, 'error': 'No wallet found'}

            privy_wallet_id = wallet.get('privy_wallet_id')
            eoa_address = wallet['address']
            
            # derive_safe_address is a sync math operation (CREATE2), no thread needed
            safe_address = wallet.get('safe_address') or self.builder.derive_safe_address(eoa_address)

            try:
                # Step 1: Check status (Synchronous RPC call)
                status = await asyncio.to_thread(self.builder.get_safe_status, eoa_address)
                
                # Step 2: Deploy Safe if needed
                if not status['deployed']:
                    logger.info(f"Deploying Safe for user {user_id}...")
                    deploy_result = await asyncio.to_thread(
                        self.builder.deploy_safe_privy, 
                        self.privy_service, privy_wallet_id, eoa_address
                    )
                    if not deploy_result['success']:
                        return {'success': False, 'error': f"Safe deployment failed: {deploy_result.get('error')}"}

                # Step 3: Always force-sync allowances
                # This is critical to fix the 'allowance: 0' error
                logger.info(f"Setting dual-exchange allowances for user {user_id}...")
                allowance_result = await asyncio.to_thread(
                    self.builder.set_allowances_privy,
                    self.privy_service, privy_wallet_id, eoa_address, safe_address
                )
                if not allowance_result['success']:
                    return {'success': False, 'error': f"Allowance update failed: {allowance_result.get('error')}"}

                # Step 4: Verify on-chain status BEFORE activating CLOB
                final_status = await asyncio.to_thread(self.builder.get_safe_status, eoa_address)
                if not final_status['allowances_set']:
                    # Note: This might be due to RPC latency. We proceed anyway because 
                    # we just sent the transaction in Step 3.
                    logger.warning(f"On-chain check for user {user_id} pending indexer. Proceeding to CLOB sync.")

                # Update DB to reflect setup completion
                await self.db.update_wallet_allowances_set(user_id, True)

                # Step 5: Force CLOB to refresh its view of your wallet
                # This makes the 0 allowance error go away on the Polymarket API side
                activation = await self._activate_trading(user_id)
                if not activation['success']:
                    return {'success': False, 'error': f"Allowances set but CLOB sync failed: {activation.get('error')}"}

                return {
                    'success': True, 
                    'safe_address': safe_address, 
                    'message': 'Wallet ready and CLOB synced.'
                }

            except Exception as e:
                logger.error(f"Setup error for user {user_id}: {e}", exc_info=True)
                return {'success': False, 'error': str(e)}

    # ── Multi-wallet helpers ─────────────────────────────────────────

    async def get_wallet(self, user_id: int) -> Optional[Dict]:
        """Get the active (or first) wallet for a user. Backward-compat shim."""
        return await self.db.get_active_user_wallet(user_id)

    async def get_wallets(self, user_id: int):
        """Get all wallets for a user, ordered by wallet_index."""
        return await self.db.get_all_user_wallets(user_id)

    async def get_wallet_by_id(self, user_id: int, wallet_db_id: int) -> Optional[Dict]:
        """Get a specific wallet by its DB id, ensuring it belongs to user_id."""
        return await self.db.get_user_wallet_by_id(wallet_db_id, user_id)

    async def count_wallets(self, user_id: int) -> int:
        return await self.db.count_user_wallets(user_id)

    async def delete_wallet(self, user_id: int) -> bool:
        """Delete the active wallet for a user (legacy / single-wallet fallback)."""
        wallet = await self.get_wallet(user_id)
        if wallet and wallet.get('id'):
            return await self.db.delete_user_wallet_by_id(user_id, wallet['id'])
        return await self.db.delete_user_wallet(user_id)

    async def delete_wallet_by_id(self, user_id: int, wallet_db_id: int) -> bool:
        """Delete a specific wallet by its DB id."""
        return await self.db.delete_user_wallet_by_id(user_id, wallet_db_id)

    async def set_active_wallet(self, user_id: int, wallet_db_id: int) -> bool:
        return await self.db.set_active_wallet(user_id, wallet_db_id)

    async def rename_wallet(self, user_id: int, wallet_db_id: int, name: str) -> bool:
        return await self.db.rename_trading_wallet(user_id, wallet_db_id, name)

    async def create_additional_wallet(self, user_id: int, wallet_name: str = None) -> Dict:
        """Create a brand-new wallet (2nd or 3rd slot) for a user."""
        count = await self.count_wallets(user_id)
        MAX_WALLETS = 3
        if count >= MAX_WALLETS:
            return {'success': False, 'error': f'You can have at most {MAX_WALLETS} wallets.'}

        try:
            privy_result = await self.privy_service.create_user_with_wallet(user_id)
            eoa_address = privy_result['wallet_address']
            privy_user_id = privy_result['privy_user_id']
            privy_wallet_id = privy_result['privy_wallet_id']

            safe_address = None
            if self.builder:
                safe_address = self.builder.derive_safe_address(eoa_address)

            wallet_db_id = await self.db.save_new_wallet(
                user_id=user_id,
                address=eoa_address.lower(),
                safe_address=safe_address,
                wallet_type='privy',
                privy_user_id=privy_user_id,
                privy_wallet_id=privy_wallet_id,
                wallet_name=wallet_name,
            )

            if wallet_db_id:
                return {
                    'success': True,
                    'address': eoa_address,
                    'safe_address': safe_address,
                    'wallet_db_id': wallet_db_id,
                }
            return {'success': False, 'error': 'Failed to save wallet.'}

        except Exception as e:
            logger.error(f"Error creating additional wallet: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    async def get_balances_for_wallet(self, wallet: Dict) -> Dict:
        if not wallet:
            return {"polymarket_usdc": 0.0, "safe_usdc": 0.0}
        safe_address = wallet.get("safe_address")
        if not safe_address:
            return {"polymarket_usdc": 0.0, "safe_usdc": 0.0}
        try:
            import requests as req

            # Fetch portfolio value from Polymarket API
            url = f"https://data-api.polymarket.com/value?user={safe_address}"
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None, lambda: req.get(url, timeout=6)
            )
            portfolio_value = 0.0
            if resp.ok:
                data = resp.json()
                if isinstance(data, list) and data:
                    portfolio_value = float(data[0].get("portfolioValue", 0) or 0)
                elif isinstance(data, dict):
                    portfolio_value = float(data.get("portfolioValue", 0) or 0)
                else:
                    portfolio_value = 0.0

            # ✅ Also check on-chain USDC.e balance (what Polymarket actually uses)
            usdc_abi = [{"constant": True, "inputs": [{"name": "owner", "type": "address"}],
                        "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}],
                        "type": "function"}]
            usdce = self.w3.eth.contract(
                address=Web3.to_checksum_address(USDC_E_ADDRESS), abi=usdc_abi
            )
            safe_cs = Web3.to_checksum_address(safe_address)
            raw_balance = await loop.run_in_executor(
                None, lambda: usdce.functions.balanceOf(safe_cs).call()
            )
            safe_usdce = float(raw_balance) / 1_000_000

            logger.info(f"Balance check for {safe_address}: portfolio={portfolio_value}, safe_usdce={safe_usdce}")
            return {
                "polymarket_usdc": portfolio_value,
                "safe_usdc": safe_usdce,
            }
        except Exception as e:
            logger.error(f"get_balances_for_wallet FAILED: {e}", exc_info=True)
            return {"polymarket_usdc": 0.0, "safe_usdc": 0.0}

    # ── CLOB client that accepts an optional wallet dict ────────────

    async def get_balances(self, user_id: int) -> Dict:
        """Get wallet balances (all RPC calls run concurrently)."""
        import asyncio

        wallet = await self.get_wallet(user_id)
        if not wallet:
            return {'success': False, 'error': 'No wallet found'}

        eoa_address = wallet['address']
        safe_address = wallet.get('safe_address')

        result = {
            'success': True,
            'eoa_address': eoa_address,
            'safe_address': safe_address,
            'eoa_usdc': 0.0,
            'eoa_usdc_e': 0.0,
            'safe_usdc': 0.0,
            'safe_usdc_e': 0.0,
            'polymarket_usdc': 0.0,
        }

        usdc_abi = [{
            "constant": True,
            "inputs": [{"name": "_owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "balance", "type": "uint256"}],
            "type": "function"
        }]

        usdc = self.w3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS), abi=usdc_abi
        )
        usdc_e = self.w3.eth.contract(
            address=Web3.to_checksum_address(USDC_E_ADDRESS), abi=usdc_abi
        )

        eoa_cs = Web3.to_checksum_address(eoa_address)

        tasks = {
            'eoa_usdc': asyncio.to_thread(usdc.functions.balanceOf(eoa_cs).call),
            'eoa_usdc_e': asyncio.to_thread(usdc_e.functions.balanceOf(eoa_cs).call),
            'polymarket_usdc': self._get_polymarket_balance(user_id),
        }

        if safe_address:
            safe_cs = Web3.to_checksum_address(safe_address)
            tasks['safe_usdc'] = asyncio.to_thread(usdc.functions.balanceOf(safe_cs).call)
            tasks['safe_usdc_e'] = asyncio.to_thread(usdc_e.functions.balanceOf(safe_cs).call)

        try:
            keys = list(tasks.keys())
            values = await asyncio.gather(*tasks.values(), return_exceptions=True)
            fetched = dict(zip(keys, values))

            for key in ['eoa_usdc', 'eoa_usdc_e', 'safe_usdc', 'safe_usdc_e']:
                val = fetched.get(key)
                if val is not None and not isinstance(val, Exception):
                    result[key] = float(val) / 1_000_000

            poly = fetched.get('polymarket_usdc')
            if poly is not None and not isinstance(poly, Exception):
                result['polymarket_usdc'] = poly

        except Exception as e:
            logger.error(f"Error getting balances: {e}")

        return result

    async def _get_polymarket_balance(self, user_id: int) -> float:
        """Get Polymarket CLOB balance"""
        try:
            client = await self._get_clob_client(user_id)
            if not client:
                return 0.0

            client.update_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )

            balance_data = client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )

            if balance_data:
                return float(balance_data.get('balance', 0)) / 1_000_000

            return 0.0

        except Exception as e:
            error_str = str(e)
            if '401' in error_str or 'Unauthorized' in error_str:
                logger.warning(
                    f"Purging stale API creds for user {user_id} after 401"
                )
                try:
                    await self.db.delete_user_api_creds(user_id)
                except Exception:
                    pass
            logger.error(f"Error fetching Polymarket balance: {e}")
            return 0.0

    async def get_positions_summary_for_wallet(self, wallet: Dict) -> Dict:
        """
        Fetch and categorise all positions for a wallet.
        Returns {
          "open": [...],
          "won": [...],   # redeemable=True, pnl > 0
          "lost": [...],  # resolved but losing (currentValue < 0.01 or pnl <= 0 and resolved)
          "success": bool
        }
        """
        try:
            safe_address = wallet.get('safe_address')
            if not safe_address:
                return {"success": False, "open": [], "won": [], "lost": []}
            loop = asyncio.get_running_loop()
            positions = await loop.run_in_executor(None, lambda: _fetch_positions(safe_address))
            open_pos, won_pos, lost_pos = [], [], []
            for pos in positions or []:
                title = pos.get("title") or pos.get("market", "Unknown market")
                pnl = float(pos.get("cashPnl", 0) or pos.get("pnl", 0) or 0.0)
                size = float(pos.get("size", 0.0) or 0.0)
                current_value = float(pos.get("currentValue", 0) or 0.0)
                redeemable = bool(pos.get("redeemable"))
                outcome = pos.get("outcome", "")
                entry = {"title": title, "pnl": pnl, "size": size, "current_value": current_value, "outcome": outcome}

                if redeemable and pnl > 0:
                    won_pos.append(entry)
                elif redeemable and pnl <= 0:
                    # Resolved but lost
                    lost_pos.append(entry)
                elif current_value < 0.01 and size > 0:
                    # Likely worthless/lost market even if redeemable flag not set yet
                    lost_pos.append(entry)
                elif current_value >= 0.01:
                    open_pos.append(entry)
            return {"success": True, "open": open_pos, "won": won_pos, "lost": lost_pos}
        except Exception as e:
            logger.error(f"Error getting positions summary: {e}", exc_info=True)
            return {"success": False, "open": [], "won": [], "lost": []}

    async def get_won_markets_for_wallet(self, wallet: Dict) -> Dict:
        """Get won/claimable markets for a specific wallet dict (multi-wallet support)."""
        result = await self.get_positions_summary_for_wallet(wallet)
        return {"success": result["success"], "markets": result.get("won", [])}

    async def get_lost_markets_for_wallet(self, wallet: Dict) -> Dict:
        """Get lost markets for a specific wallet dict."""
        result = await self.get_positions_summary_for_wallet(wallet)
        return {"success": result["success"], "markets": result.get("lost", [])}

    async def get_won_markets(self, user_id: int) -> Dict:
        """
        Get a summary of markets the user has won but may or may not have claimed yet.
        Returns:
          {
            "success": bool,
            "markets": [
              {"title": str, "pnl": float, "size": float, "redeemable": bool}
            ]
          }
        """
        import asyncio

        try:
            wallet = await self.get_wallet(user_id)
            if not wallet:
                return {"success": False, "error": "No wallet found", "markets": []}

            safe_address = wallet.get('safe_address')
            if not safe_address:
                return {"success": False, "error": "No Safe address found", "markets": []}

            loop = asyncio.get_running_loop()
            positions = await loop.run_in_executor(
                None, lambda: _fetch_positions(safe_address)
            )

            markets = []

            for pos in positions or []:
                # You may need to tweak these keys once you see real data
                title = pos.get("title") or pos.get("market", "Unknown market")
                pnl = float(pos.get("cashPnl", 0) or pos.get("pnl", 0) or 0.0)
                size = float(pos.get("size", 0.0) or 0.0)
                redeemable = bool(pos.get("redeemable"))

                # Only show markets that are truly won: resolved (redeemable) AND profitable
                if redeemable and pnl > 0:
                    markets.append(
                        {
                            "title": title,
                            "pnl": pnl,
                            "size": size,
                            "redeemable": redeemable,
                        }
                    )

            return {"success": True, "markets": markets}

        except Exception as e:
            logger.error(f"Error getting won markets for user {user_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e), "markets": []}


    async def deposit_to_safe(self, user_id: int, amount: float) -> Dict:
        """Transfer USDC from EOA to Safe (gasless via Privy signing)."""
        if not self.builder:
            return {'success': False, 'error': 'Builder not configured'}

        wallet = await self.get_wallet(user_id)
        if not wallet:
            return {'success': False, 'error': 'No wallet found'}

        privy_wallet_id = wallet.get('privy_wallet_id')
        if not privy_wallet_id:
            return {'success': False, 'error': 'No Privy wallet configured'}

        safe_address = wallet.get('safe_address')
        if not safe_address:
            return {'success': False, 'error': 'Safe not set up'}

        eoa_address = wallet['address']
        return self.builder.transfer_usdc_to_safe_privy(
            self.privy_service, privy_wallet_id, eoa_address, safe_address, amount
        )

    async def withdraw_usdc(self, user_id: int, to_address: str, amount: float) -> Dict:
        """Withdraw USDC from Safe to external address (gasless via Privy signing)."""
        if not self.builder:
            return {'success': False, 'error': 'Builder not configured'}

        wallet = await self.get_wallet(user_id)
        if not wallet:
            return {'success': False, 'error': 'No wallet found'}

        privy_wallet_id = wallet.get('privy_wallet_id')
        if not privy_wallet_id:
            return {'success': False, 'error': 'No Privy wallet configured'}

        safe_address = wallet.get('safe_address')
        if not safe_address:
            return {'success': False, 'error': 'Safe not set up'}

        if not to_address.startswith('0x') or len(to_address) != 42:
            return {'success': False, 'error': 'Invalid destination address'}

        balances = await self.get_balances(user_id)
        available = balances.get('safe_usdc', 0) + balances.get('polymarket_usdc', 0)

        if amount > available:
            return {
                'success': False,
                'error': f'Insufficient balance. Available: ${available:.2f}',
            }

        eoa_address = wallet['address']
        return self.builder.withdraw_from_safe_privy(
            self.privy_service, privy_wallet_id, eoa_address, safe_address, to_address, amount
        )

    async def claim_winnings(self, user_id: int) -> Dict:
        """
        Fetch all resolved winning positions and redeem them via the CTF contract
        using the gasless Builder Relayer (Privy-backed).

        Returns a summary of claimed markets and total USDC received.
        """
        if not self.builder:
            return {'success': False, 'error': 'Builder Relayer not configured'}

        wallet = await self.get_wallet(user_id)
        if not wallet:
            return {'success': False, 'error': 'No wallet found'}

        privy_wallet_id = wallet.get('privy_wallet_id')
        if not privy_wallet_id:
            return {'success': False, 'error': 'No Privy wallet configured'}

        safe_address = wallet.get('safe_address')
        eoa_address = wallet.get('address')
        if not safe_address:
            return {'success': False, 'error': 'Safe not set up. Run /setup first.'}

        try:
            # Step 1: Fetch all positions from the Polymarket data API
            loop = asyncio.get_running_loop()
            positions = await loop.run_in_executor(None, lambda: _fetch_positions(safe_address))

            if not positions:
                return {
                    'success': True,
                    'claimed': [],
                    'total_claimed': 0.0,
                    'message': 'No open positions found.'
                }

            # Step 2: Filter for truly redeemable winning positions
            # redeemable=True AND pnl > 0 means the position is won and claimable
            redeemable = []
            for pos in positions:
                pnl = float(pos.get('cashPnl', 0) or pos.get('pnl', 0) or 0.0)
                if pos.get('redeemable') and pnl > 0:
                    redeemable.append(pos)

            if not redeemable:
                return {
                    'success': True,
                    'claimed': [],
                    'total_claimed': 0.0,
                    'message': 'No redeemable winning positions found.'
                }

            # Step 3: Redeem each position via CTF.redeemPositions (gasless)
            claimed = []
            failed = []

            for pos in redeemable:
                # ── Replace this block inside `for pos in redeemable:` ──

                market_title = pos.get("title") or pos.get("market", "Unknown market")
                pnl = float(pos.get("cashPnl", 0) or pos.get("pnl", 0) or 0.0)

                try:
                    condition_id = (
                        pos.get("conditionId") or pos.get("conditionid")
                        or pos.get("marketId")
                    )
                    if not condition_id:
                        logger.warning(f"No conditionid for position {market_title}")
                        failed.append(market_title)
                        continue
                  
                    outcome_index = pos.get("outcomeIndex", 0)

                    neg_risk = bool(pos.get("negativeRisk", False))

                    collateral_token = USDC_E_ADDRESS

                    token_size = float(pos.get("size", 0) or 0)

                    logger.info(
                        f"Redeem: {market_title}, cid={condition_id[:16]}, "
                        f"neg_risk={neg_risk}, outcome_index={outcome_index}, "
                        f"collateral={'USDC.e' if collateral_token == USDC_E_ADDRESS else 'native'}, "
                        f"size={token_size}"
                    )

                    result = await asyncio.to_thread(
                        self.builder.redeem_positions_privy,
                        self.privy_service,
                        privy_wallet_id,
                        eoa_address,
                        safe_address,
                        condition_id,
                        outcome_index,
                        token_size,
                        neg_risk,
                        collateral_token,  # ✅ Now passing the correct collateral
                    )

                    if result.get('success'):
                        claimed.append({
                            'market': market_title,
                            'amount': pnl,
                            'condition_id': condition_id,
                            'tx_hash': result.get('tx_hash', ''),
                        })
                        logger.info(f"Claimed position for user {user_id}: {market_title} (+${pnl:.2f})")
                    else:
                        logger.warning(f"Failed to redeem {market_title}: {result.get('error')}")
                        failed.append(market_title)

                except Exception as e:
                    logger.warning(f"Failed to redeem position {market_title}: {e}")
                    failed.append(market_title)

            total_claimed = sum(p['amount'] for p in claimed)

            return {
                'success': True,
                'claimed': claimed,
                'failed': failed,
                'total_claimed': total_claimed,
                'message': f"Claimed {len(claimed)} position(s), {len(failed)} failed."
            }

        except Exception as e:
            logger.error(f"Error claiming winnings for user {user_id}: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}


    async def get_wallet_status(self, user_id: int) -> Dict:
        """Get complete wallet status (runs builder check in thread)."""
        import asyncio

        wallet = await self.get_wallet(user_id)
        if not wallet:
            return {'has_wallet': False}

        eoa_address = wallet['address']

        status = {
            'has_wallet': True,
            'eoa_address': eoa_address,
            'safe_address': None,
            'safe_deployed': False,
            'allowances_set': False,
            'ready_to_trade': False,
        }

        if self.builder:
            safe_status = await asyncio.to_thread(
                self.builder.get_safe_status, eoa_address
            )
            status['safe_address'] = safe_status['safe_address']
            status['safe_deployed'] = safe_status['deployed']
            status['allowances_set'] = safe_status['allowances_set']
            status['ready_to_trade'] = (
                safe_status['deployed'] and safe_status['allowances_set']
            )

        return status

    async def _activate_trading(self, user_id: int) -> Dict:
        """
        Derive CLOB API creds and sync on-chain allowances to Polymarket.
        Must be called after setup_safe to make the wallet trade-ready.
        """
        try:
            import asyncio

            client = await self._get_clob_client(user_id)
            if not client:
                return {'success': False, 'error': 'Could not create CLOB client'}

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: client.update_balance_allowance(
                    BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                ),
            )

            logger.info(f"Trading activated for user {user_id}: API creds derived + allowances synced")
            return {'success': True}

        except Exception as e:
            logger.warning(f"Trading activation failed for user {user_id}: {e}")
            return {'success': False, 'error': str(e)}

    async def get_balance(self, userid: int, force_refresh: bool = False) -> float:
        balances = await self.get_balances(userid)
        return (balances.get("safe_usdc", 0)
                + balances.get("safe_usdce", 0)
                + balances.get("polymarket_usdc", 0))
