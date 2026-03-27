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
USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

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

        # Inject Privy signer with factory address so sign() can reconstruct typed data
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
        """Set USDC allowances for BOTH Polymarket exchanges using Privy-backed signing."""
        try:
            client, _ = self._get_relay_client_privy(privy_service, wallet_id, eoa_address)

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
            
            # 1. Create calldata for Standard CTF Exchange
            calldata_ctf = usdc.encode_abi("approve", [
                Web3.to_checksum_address(CTF_EXCHANGE), MAX_UINT256
            ])
            
            # 2. Create calldata for NegRisk Exchange
            calldata_negrisk = usdc.encode_abi("approve", [
                Web3.to_checksum_address(NEG_RISK_CTF_EXCHANGE), MAX_UINT256
            ])

            from py_builder_relayer_client.models import SafeTransaction, OperationType
            
            # 3. Create two transactions
            tx_ctf = SafeTransaction(to=USDC_ADDRESS, data=calldata_ctf, value="0", operation=OperationType.Call)
            tx_negrisk = SafeTransaction(to=USDC_ADDRESS, data=calldata_negrisk, value="0", operation=OperationType.Call)

            # 4. Execute them as a single BATCH
            response = client.execute([tx_ctf, tx_negrisk], "Approve both Polymarket exchanges")
            
            result = client.poll_until_state(
                transaction_id=response.transaction_id,
                states=["STATE_CONFIRMED"],
                fail_state="STATE_FAILED",
                max_polls=20,
                poll_frequency=3000,
            )

            if result:
                logger.info(f"✅ Both allowances set for Safe {safe_address} (Privy-backed)")
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

    def get_safe_status(self, eoa_address: str) -> Dict:
            """
            Get the status of a Safe for an EOA, checking allowances for BOTH CTF exchanges.
            """
            safe_address = self.derive_safe_address(eoa_address)
            deployed = self.is_safe_deployed(safe_address) if safe_address else False
            
            # Check allowances if deployed
            allowances_set = False
            if deployed and safe_address:
                try:
                    usdc_abi = [{
                        "constant": True,
                        "inputs": [
                            {"name": "owner", "type": "address"},
                            {"name": "spender", "type": "address"}
                        ],
                        "name": "allowance",
                        "outputs": [{"name": "", "type": "uint256"}],
                        "type": "function"
                    }]
                    
                    usdc = self.w3.eth.contract(
                        address=Web3.to_checksum_address(USDC_ADDRESS),
                        abi=usdc_abi
                    )
                    
                    allowance_ctf = usdc.functions.allowance(
                        Web3.to_checksum_address(safe_address),
                        Web3.to_checksum_address(CTF_EXCHANGE)
                    ).call()
                    
                    # Check NegRisk
                    allowance_negrisk = usdc.functions.allowance(
                        Web3.to_checksum_address(safe_address),
                        Web3.to_checksum_address(NEG_RISK_CTF_EXCHANGE)
                    ).call()
                    
                    # Allowances are set only if BOTH are > 1M USDC
                    allowances_set = (allowance_ctf > 10**12) and (allowance_negrisk > 10**12)
                    
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