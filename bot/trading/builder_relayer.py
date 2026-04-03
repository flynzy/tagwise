# bot/trading/builder_relayer.py
"""
Polymarket Builder Relayer client for gasless operations.
Uses official py-builder-relayer-client.
"""

import os
import logging
from typing import Optional, Dict
from eth_account import Account
from web3 import Web3

logger = logging.getLogger(__name__)

# Contract addresses on Polygon
USDC_ADDRESS   = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # native USDC
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e (PoS-bridged)
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
CTF_EXCHANGE          = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# All USDC tokens that may be used on Polymarket — we approve them all
ALL_USDC_TOKENS = [USDC_ADDRESS, USDC_E_ADDRESS]
# All Polymarket exchange spenders
ALL_EXCHANGES   = [CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE]

RELAYER_URL = "https://relayer-v2.polymarket.com/"
CHAIN_ID = 137


class BuilderRelayer:
    """
    Client for Polymarket's Builder Relayer API.
    Enables gasless operations for Safe wallets.
    """
    
    def __init__(
        self,
        api_key: str = None,
        api_secret: str = None,
        api_passphrase: str = None,
        rpc_url: str = None
    ):
        self.api_key = api_key or os.getenv("POLYMARKET_BUILDER_API_KEY")
        self.api_secret = api_secret or os.getenv("POLYMARKET_BUILDER_SECRET")
        self.api_passphrase = api_passphrase or os.getenv("POLYMARKET_BUILDER_PASSPHRASE")
        
        if not all([self.api_key, self.api_secret, self.api_passphrase]):
            raise ValueError(
                "Builder credentials required. Set POLYMARKET_BUILDER_API_KEY, "
                "POLYMARKET_BUILDER_SECRET, and POLYMARKET_BUILDER_PASSPHRASE"
            )
        
        self.rpc_url = rpc_url or os.getenv("POLYGON_RPC")
        if not self.rpc_url:
            raise ValueError("POLYGON_RPC env var not set")
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        
        # Add PoA middleware for Polygon
        try:
            from web3.middleware import ExtraDataToPOAMiddleware
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except ImportError:
            from web3.middleware import geth_poa_middleware
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    
    def _get_relay_client(self, private_key: str):
        """Create a RelayClient instance for the given private key"""
        try:
            from py_builder_relayer_client.client import RelayClient
            from py_builder_signing_sdk.config import BuilderConfig
            from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
        except ImportError as e:
            raise ImportError(
                f"Required packages not installed: {e}. "
                "Run: pip install py-builder-relayer-client py-builder-signing-sdk"
            )
        
        # ✅ FIXED: Use positional arguments instead of keyword arguments
        creds = BuilderApiKeyCreds(
            self.api_key,
            self.api_secret,
            self.api_passphrase
        )
        
        # Create builder config with local credentials
        builder_config = BuilderConfig(local_builder_creds=creds)
        
        # Create account from private key
        account = Account.from_key(private_key)
        
        # Create relay client
        client = RelayClient(
            relayer_url=RELAYER_URL,
            chain_id=CHAIN_ID,
            private_key=private_key,
            builder_config=builder_config
        )
        
        return client, account.address


    def derive_safe_address(self, eoa_address: str) -> str:
        """
        Derive the Safe/proxy address for an EOA.
        """
        try:
            from py_builder_relayer_client.builder.derive import derive
            from py_builder_relayer_client.config import get_contract_config
            
            config = get_contract_config(CHAIN_ID)
            safe_address = derive(eoa_address, config.safe_factory)
            return safe_address.lower() if safe_address else None
        except Exception as e:
            logger.error(f"Error deriving Safe address: {e}")
            # Fallback to factory method
            return self._derive_safe_from_factory(eoa_address)
    
    def _derive_safe_from_factory(self, eoa_address: str) -> str:
        """Derive Safe address using the factory contract"""
        SAFE_FACTORY_ADDRESS = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052"
        
        factory_abi = [{
            "inputs": [{"name": "owner", "type": "address"}],
            "name": "getAddress",
            "outputs": [{"name": "", "type": "address"}],
            "stateMutability": "view",
            "type": "function"
        }]
        
        try:
            factory = self.w3.eth.contract(
                address=Web3.to_checksum_address(SAFE_FACTORY_ADDRESS),
                abi=factory_abi
            )
            safe_address = factory.functions.getAddress(
                Web3.to_checksum_address(eoa_address)
            ).call()
            return safe_address.lower()
        except Exception as e:
            logger.error(f"Error calling factory: {e}")
            return None
    
    def is_safe_deployed(self, safe_address: str) -> bool:
        """Check if a Safe is already deployed"""
        try:
            code = self.w3.eth.get_code(Web3.to_checksum_address(safe_address))
            return len(code) > 0
        except Exception as e:
            logger.error(f"Error checking Safe deployment: {e}")
            return False
    
    def deploy_safe(self, private_key: str) -> Dict:
        try:
            client, eoa_address = self._get_relay_client(private_key)
            safe_address = client.get_expected_safe()

            if client.get_deployed(safe_address):
                logger.info(f"Safe already deployed at {safe_address}")
                return {'success': True, 'safe_address': safe_address.lower(), 'already_deployed': True}

            logger.info(f"Deploying Safe for {eoa_address}...")
            response = client.deploy()
            tx_id = response.transaction_id
            tx_hash = response.transaction_hash

            # ✅ FIXED: use STATE_ prefix strings, no enum import needed
            result = client.poll_until_state(
                transaction_id=tx_id,
                states=["STATE_CONFIRMED"],   # ✅ was "CONFIRMED"
                fail_state="STATE_FAILED",    # ✅ was "FAILED"
                max_polls=20,
                poll_frequency=3000
            )

            if result:
                logger.info(f"Safe deployed at {safe_address}")
                return {'success': True, 'safe_address': safe_address.lower(), 'tx_hash': tx_hash, 'already_deployed': False}
            else:
                return {'success': False, 'error': 'Deployment timed out or failed'}

        except Exception as e:
            logger.error(f"Error deploying Safe: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    
    def set_allowances(self, private_key: str, safe_address: str) -> Dict:
        try:
            client, eoa_address = self._get_relay_client(private_key)

            MAX_UINT256 = 2**256 - 1
            usdc_abi = [{
                "name": "approve", "type": "function",
                "inputs": [
                    {"name": "spender", "type": "address"},
                    {"name": "amount", "type": "uint256"}
                ],
                "outputs": [{"name": "", "type": "bool"}],
                "stateMutability": "nonpayable"
            }]
            usdc = self.w3.eth.contract(
                address=Web3.to_checksum_address(USDC_ADDRESS), abi=usdc_abi
            )
            calldata = usdc.encode_abi("approve", [
                Web3.to_checksum_address(NEG_RISK_CTF_EXCHANGE), MAX_UINT256
            ])

            # ✅ Correct import path confirmed from safe.py source
            from py_builder_relayer_client.models import SafeTransaction, OperationType

            txn = SafeTransaction(
                to=USDC_ADDRESS,
                data=calldata,
                value="0",
                operation=OperationType.Call
            )

            response = client.execute([txn], "Approve USDC for trading")

            result = client.poll_until_state(
                transaction_id=response.transaction_id,
                states=["STATE_CONFIRMED"],
                fail_state="STATE_FAILED",
                max_polls=20,
                poll_frequency=3000
            )

            if result:
                logger.info(f"✅ Allowances set for Safe {safe_address}")
                return {'success': True, 'tx_hash': response.transaction_hash}
            return {'success': False, 'error': 'Allowance tx timed out'}

        except Exception as e:
            logger.error(f"Error setting allowances: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def withdraw_from_safe(self, private_key: str, safe_address: str, to_address: str, amount: float) -> Dict:
        """Withdraw USDC from Safe to an external address (gasless)."""
        try:
            client, eoa_address = self._get_relay_client(private_key)
            amount_units = int(amount * 1_000_000)

            usdc_abi = [{
                "name": "transfer", "type": "function",
                "inputs": [
                    {"name": "to", "type": "address"},
                    {"name": "amount", "type": "uint256"}
                ],
                "outputs": [{"name": "", "type": "bool"}],
                "stateMutability": "nonpayable"
            }]
            usdc = self.w3.eth.contract(
                address=Web3.to_checksum_address(USDC_ADDRESS), abi=usdc_abi
            )
            calldata = usdc.encode_abi("transfer", [
                Web3.to_checksum_address(to_address), amount_units
            ])

            from py_builder_relayer_client.models import SafeTransaction, OperationType
            txn = SafeTransaction(to=USDC_ADDRESS, data=calldata, value="0", operation=OperationType.Call)
            response = client.execute([txn], f"Withdraw {amount:.2f} USDC")

            result = client.poll_until_state(
                transaction_id=response.transaction_id,
                states=["STATE_CONFIRMED"], fail_state="STATE_FAILED",
                max_polls=20, poll_frequency=3000
            )
            if result:
                logger.info(f"✅ Withdrew {amount:.2f} USDC from Safe {safe_address} to {to_address}")
                return {'success': True, 'tx_hash': response.transaction_hash, 'to_address': to_address, 'amount': amount}
            return {'success': False, 'error': 'Withdrawal tx timed out'}

        except Exception as e:
            logger.error(f"Error withdrawing from Safe: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}


    def transfer_usdc_to_safe(self, private_key: str, safe_address: str, amount: float) -> Dict:
        """Transfer USDC from EOA to Safe (gasless)."""
        try:
            client, eoa_address = self._get_relay_client(private_key)
            amount_units = int(amount * 1_000_000)

            usdc_abi = [{
                "name": "transfer", "type": "function",
                "inputs": [
                    {"name": "to", "type": "address"},
                    {"name": "amount", "type": "uint256"}
                ],
                "outputs": [{"name": "", "type": "bool"}],
                "stateMutability": "nonpayable"
            }]
            usdc = self.w3.eth.contract(
                address=Web3.to_checksum_address(USDC_ADDRESS), abi=usdc_abi
            )
            calldata = usdc.encode_abi("transfer", [
                Web3.to_checksum_address(safe_address), amount_units
            ])

            from py_builder_relayer_client.models import SafeTransaction, OperationType
            txn = SafeTransaction(to=USDC_ADDRESS, data=calldata, value="0", operation=OperationType.Call)
            response = client.execute([txn], f"Deposit {amount:.2f} USDC to Safe")

            result = client.poll_until_state(
                transaction_id=response.transaction_id,
                states=["STATE_CONFIRMED"], fail_state="STATE_FAILED",
                max_polls=20, poll_frequency=3000
            )
            if result:
                logger.info(f"✅ Deposited {amount:.2f} USDC to Safe {safe_address}")
                return {'success': True, 'tx_hash': response.transaction_hash, 'amount': amount}
            return {'success': False, 'error': 'Deposit tx timed out'}

        except Exception as e:
            logger.error(f"Error depositing to Safe: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}


    # ─── Privy-backed methods (no raw private key) ─────────────────────

    def _get_relay_client_privy(self, privy_service, wallet_id: str, eoa_address: str):
        """Create a RelayClient with a Privy-backed signer instead of raw key."""
        try:
            from py_builder_relayer_client.client import RelayClient
            from py_builder_relayer_client.config import get_contract_config
            from py_builder_signing_sdk.config import BuilderConfig
            from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
        except ImportError as e:
            raise ImportError(
                f"Required packages not installed: {e}. "
                "Run: pip install py-builder-relayer-client py-builder-signing-sdk"
            )

        from bot.trading.privy_signers import PrivyRelayerSigner

        creds = BuilderApiKeyCreds(
            self.api_key, self.api_secret, self.api_passphrase
        )
        builder_config = BuilderConfig(local_builder_creds=creds)

        # Get contract config to extract safe_factory address
        contract_config = get_contract_config(CHAIN_ID)

        client = RelayClient(
            relayer_url=RELAYER_URL,
            chain_id=CHAIN_ID,
            private_key=None, 
            builder_config=builder_config,
        )
        
        # CRITICAL: Manually set the address property so the client
        # targets the correct Safe derivation
        client.address = Web3.to_checksum_address(eoa_address) 
        
        client.signer = PrivyRelayerSigner(
            privy_service,
            wallet_id,
            eoa_address,
            CHAIN_ID,
            safe_factory=contract_config.safe_factory,
        )
        return client, eoa_address

    def deploy_safe_privy(self, privy_service, wallet_id: str, eoa_address: str) -> Dict:
        """Deploy Safe using Privy-backed signing."""
        try:
            client, _ = self._get_relay_client_privy(privy_service, wallet_id, eoa_address)
            safe_address = client.get_expected_safe()

            if client.get_deployed(safe_address):
                logger.info(f"Safe already deployed at {safe_address}")
                return {'success': True, 'safe_address': safe_address.lower(), 'already_deployed': True}

            logger.info(f"Deploying Safe for {eoa_address} (Privy-backed)...")
            response = client.deploy()
            tx_id = response.transaction_id
            tx_hash = response.transaction_hash

            result = client.poll_until_state(
                transaction_id=tx_id,
                states=["STATE_CONFIRMED"],
                fail_state="STATE_FAILED",
                max_polls=20,
                poll_frequency=3000,
            )

            if result:
                logger.info(f"Safe deployed at {safe_address}")
                return {'success': True, 'safe_address': safe_address.lower(), 'tx_hash': tx_hash, 'already_deployed': False}
            else:
                return {'success': False, 'error': 'Deployment timed out or failed'}

        except Exception as e:
            logger.error(f"Error deploying Safe (Privy): {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def set_allowances_privy(self, privy_service, wallet_id: str, eoa_address: str, safe_address: str) -> Dict:
        """
        Approve BOTH USDC variants (native USDC + USDC.e) for BOTH Polymarket exchanges.
        This creates 4 approve() transactions batched into a single relayer call.

        Why both tokens?  The Safe wallet may hold either/both depending on how the
        user deposited funds.  Polymarket's CLOB checks allowance for the token it
        tracks; if the Safe holds USDC.e the CTF Exchange allowance must be set on
        USDC.e, not on native USDC.
        """
        try:
            client, _ = self._get_relay_client_privy(privy_service, wallet_id, eoa_address)

            MAX_UINT256 = 2**256 - 1
            approve_abi = [{
                "name": "approve", "type": "function",
                "inputs": [
                    {"name": "spender", "type": "address"},
                    {"name": "amount", "type": "uint256"}
                ],
                "outputs": [{"name": "", "type": "bool"}],
                "stateMutability": "nonpayable"
            }]

            from py_builder_relayer_client.models import SafeTransaction, OperationType

            txns = []
            for token_addr in ALL_USDC_TOKENS:
                token_contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(token_addr), abi=approve_abi
                )
                for spender_addr in ALL_EXCHANGES:
                    calldata = token_contract.encode_abi("approve", [
                        Web3.to_checksum_address(spender_addr), MAX_UINT256
                    ])
                    txns.append(SafeTransaction(
                        to=token_addr, data=calldata, value="0", operation=OperationType.Call
                    ))

            logger.info(
                f"Submitting {len(txns)} approve() txns for Safe {safe_address} "
                f"(tokens: {[t[:10] for t in ALL_USDC_TOKENS]}, "
                f"spenders: {[s[:10] for s in ALL_EXCHANGES]})"
            )
            response = client.execute(txns, "Approve USDC + USDC.e for all Polymarket exchanges")

            result = client.poll_until_state(
                transaction_id=response.transaction_id,
                states=["STATE_CONFIRMED"],
                fail_state="STATE_FAILED",
                max_polls=20,
                poll_frequency=3000,
            )

            if result:
                logger.info(f"✅ All allowances set for Safe {safe_address} (Privy-backed)")
                return {'success': True, 'tx_hash': response.transaction_hash}
            return {'success': False, 'error': 'Allowance batch tx timed out'}

        except Exception as e:
            logger.error(f"Error setting allowances (Privy): {e}", exc_info=True)
            return {'success': False, 'error': str(e)}
            

    def withdraw_from_safe_privy(self, privy_service, wallet_id: str, eoa_address: str,
                                  safe_address: str, to_address: str, amount: float) -> Dict:
        """Withdraw USDC from Safe using Privy-backed signing."""
        try:
            client, _ = self._get_relay_client_privy(privy_service, wallet_id, eoa_address)
            amount_units = int(amount * 1_000_000)

            usdc_abi = [{
                "name": "transfer", "type": "function",
                "inputs": [
                    {"name": "to", "type": "address"},
                    {"name": "amount", "type": "uint256"}
                ],
                "outputs": [{"name": "", "type": "bool"}],
                "stateMutability": "nonpayable"
            }]
            usdc = self.w3.eth.contract(
                address=Web3.to_checksum_address(USDC_ADDRESS), abi=usdc_abi
            )
            calldata = usdc.encode_abi("transfer", [
                Web3.to_checksum_address(to_address), amount_units
            ])

            from py_builder_relayer_client.models import SafeTransaction, OperationType
            txn = SafeTransaction(to=USDC_ADDRESS, data=calldata, value="0", operation=OperationType.Call)
            response = client.execute([txn], f"Withdraw {amount:.2f} USDC")

            result = client.poll_until_state(
                transaction_id=response.transaction_id,
                states=["STATE_CONFIRMED"], fail_state="STATE_FAILED",
                max_polls=20, poll_frequency=3000,
            )
            if result:
                logger.info(f"Withdrew {amount:.2f} USDC from Safe {safe_address} to {to_address} (Privy)")
                return {'success': True, 'tx_hash': response.transaction_hash, 'to_address': to_address, 'amount': amount}
            return {'success': False, 'error': 'Withdrawal tx timed out'}

        except Exception as e:
            logger.error(f"Error withdrawing from Safe (Privy): {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def transfer_usdc_to_safe_privy(self, privy_service, wallet_id: str, eoa_address: str,
                                     safe_address: str, amount: float) -> Dict:
        """Transfer USDC from EOA to Safe using Privy-backed signing."""
        try:
            client, _ = self._get_relay_client_privy(privy_service, wallet_id, eoa_address)
            amount_units = int(amount * 1_000_000)

            usdc_abi = [{
                "name": "transfer", "type": "function",
                "inputs": [
                    {"name": "to", "type": "address"},
                    {"name": "amount", "type": "uint256"}
                ],
                "outputs": [{"name": "", "type": "bool"}],
                "stateMutability": "nonpayable"
            }]
            usdc = self.w3.eth.contract(
                address=Web3.to_checksum_address(USDC_ADDRESS), abi=usdc_abi
            )
            calldata = usdc.encode_abi("transfer", [
                Web3.to_checksum_address(safe_address), amount_units
            ])

            from py_builder_relayer_client.models import SafeTransaction, OperationType
            txn = SafeTransaction(to=USDC_ADDRESS, data=calldata, value="0", operation=OperationType.Call)
            response = client.execute([txn], f"Deposit {amount:.2f} USDC to Safe")

            result = client.poll_until_state(
                transaction_id=response.transaction_id,
                states=["STATE_CONFIRMED"], fail_state="STATE_FAILED",
                max_polls=20, poll_frequency=3000,
            )
            if result:
                logger.info(f"Deposited {amount:.2f} USDC to Safe {safe_address} (Privy)")
                return {'success': True, 'tx_hash': response.transaction_hash, 'amount': amount}
            return {'success': False, 'error': 'Deposit tx timed out'}

        except Exception as e:
            logger.error(f"Error depositing to Safe (Privy): {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def redeem_positions_privy(
        self,
        privy_service,
        wallet_id: str,
        eoa_address: str,
        safe_address: str,
        condition_id: str,
        outcome_index: int,
        token_size: float = 0.0,
        neg_risk: bool = False,
        collateral_token: str = None,
    ) -> Dict:
        """
        Redeem a resolved winning CTF or NegRisk position gaslessly.

        For standard binary markets:
            Calls CTF.redeemPositions(collateral, parentCollectionId, conditionId, [1, 2])

        For negRisk (multi-outcome) markets:
            Calls NegRiskAdapter.redeemPositions(questionId, amounts)
            where amounts[outcomeIndex] = token_size * 1e6

        Parameters
        ----------
        condition_id  : bytes32 hex condition/question ID
        outcome_index : 0 = YES/Up, 1 = NO/Down, etc.
        token_size    : decimal size of tokens held (for negRisk; e.g. 6.4)
        neg_risk      : True if this is a negRisk/multi-outcome market
        collateral_token: override USDC address (defaults to native USDC)
        """
        # Standard CTF contract on Polygon (Gnosis Conditional Token Framework)
        CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        # NegRisk Adapter / NegRisk CTF Exchange on Polygon
        # This contract owns the ABI: redeemPositions(bytes32 questionId, uint256[] amounts)
        NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

        CTF_REDEEM_ABI = [{
            "name": "redeemPositions",
            "type": "function",
            "inputs": [
                {"name": "collateralToken", "type": "address"},
                {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "indexSets", "type": "uint256[]"},
            ],
            "outputs": [],
            "stateMutability": "nonpayable",
        }]

        NEG_RISK_REDEEM_ABI = [{
            "name": "redeemPositions",
            "type": "function",
            "inputs": [
                {"name": "questionId", "type": "bytes32"},
                {"name": "amounts", "type": "uint256[]"},
            ],
            "outputs": [],
            "stateMutability": "nonpayable",
        }]

        try:
            client, _ = self._get_relay_client_privy(privy_service, wallet_id, eoa_address)

            # Normalise condition_id to bytes32 bytes
            cid = condition_id if condition_id.startswith("0x") else "0x" + condition_id
            cid_bytes = bytes.fromhex(cid[2:].zfill(64))

            from py_builder_relayer_client.models import SafeTransaction, OperationType

            if neg_risk:
                # ── NegRisk path ───────────────────────────────────────────
                # Build amounts array: only the held outcome slot is non-zero.
                # The adapter burns those tokens and releases USDC for the winning side.
                # Use a 2-element array (binary market); use max uint256 to drain all.
                MAX = 2**256 - 1
                # We want to redeem everything we hold for our outcome_index
                if token_size and token_size > 0:
                    token_units = int(token_size * 1_000_000)  # USDC has 6 decimals
                else:
                    token_units = MAX  # fallback: pass max to drain all

                # Build amounts: set our outcome slot, 0 for the other
                num_outcomes = max(outcome_index + 1, 2)
                amounts = [0] * num_outcomes
                amounts[outcome_index] = token_units

                adapter = self.w3.eth.contract(
                    address=Web3.to_checksum_address(NEG_RISK_ADAPTER),
                    abi=NEG_RISK_REDEEM_ABI,
                )
                calldata = adapter.encode_abi("redeemPositions", [cid_bytes, amounts])
                target = NEG_RISK_ADAPTER
                label = f"NegRisk redeem qid={condition_id[:12]}..."
            else:
                # ── Standard CTF path ─────────────────────────────────────
                token = collateral_token or USDC_ADDRESS
                parent_collection_id = b"\x00" * 32

                # Always pass [1, 2] — the CTF pays out only the winning side
                ctf = self.w3.eth.contract(
                    address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_REDEEM_ABI
                )
                calldata = ctf.encode_abi("redeemPositions", [
                    Web3.to_checksum_address(token),
                    parent_collection_id,
                    cid_bytes,
                    [1, 2],
                ])
                target = CTF_ADDRESS
                label = f"CTF redeem cid={condition_id[:12]}..."

            txn = SafeTransaction(to=target, data=calldata, value="0", operation=OperationType.Call)
            response = client.execute([txn], label)

            result = client.poll_until_state(
                transaction_id=response.transaction_id,
                states=["STATE_CONFIRMED"],
                fail_state="STATE_FAILED",
                max_polls=20,
                poll_frequency=3000,
            )
            if result:
                logger.info(f"✅ Redeemed {'NegRisk' if neg_risk else 'CTF'} position {condition_id[:12]} for Safe {safe_address}")
                return {"success": True, "tx_hash": response.transaction_hash}
            return {"success": False, "error": "Redeem tx timed out"}

        except Exception as e:
            logger.error(f"Error redeeming position (neg_risk={neg_risk}): {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def get_safe_status(self, eoa_address: str) -> Dict:
            """
            Get the status of a Safe for an EOA, checking allowances for BOTH CTF exchanges.
            """
            safe_address = self.derive_safe_address(eoa_address)
            deployed = self.is_safe_deployed(safe_address) if safe_address else False
            
            # Check allowances if deployed — must be set on ALL token/spender combinations.
            # The wallet may hold either native USDC or USDC.e, so we check both.
            allowances_set = False
            if deployed and safe_address:
                try:
                    allowance_abi = [{
                        "constant": True,
                        "inputs": [
                            {"name": "owner", "type": "address"},
                            {"name": "spender", "type": "address"}
                        ],
                        "name": "allowance",
                        "outputs": [{"name": "", "type": "uint256"}],
                        "type": "function"
                    }]

                    safe_cs = Web3.to_checksum_address(safe_address)
                    all_ok = True
                    for token_addr in ALL_USDC_TOKENS:
                        token_contract = self.w3.eth.contract(
                            address=Web3.to_checksum_address(token_addr),
                            abi=allowance_abi
                        )
                        for spender_addr in ALL_EXCHANGES:
                            val = token_contract.functions.allowance(
                                safe_cs,
                                Web3.to_checksum_address(spender_addr)
                            ).call()
                            if val == 0:
                                all_ok = False
                                logger.debug(
                                    f"Allowance=0: token={token_addr[:10]}... "
                                    f"spender={spender_addr[:10]}..."
                                )

                    allowances_set = all_ok

                except Exception as e:
                    logger.debug(f"Error checking allowances: {e}")
            
            return {
                'safe_address': safe_address,
                'deployed': deployed,
                'allowances_set': allowances_set
            }

# Singleton instance
_builder_relayer = None

def get_builder_relayer() -> BuilderRelayer:
    """Get or create the Builder Relayer singleton"""
    global _builder_relayer
    if _builder_relayer is None:
        _builder_relayer = BuilderRelayer()
    return _builder_relayer