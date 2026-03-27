# tests/test_allowance.py
# Check on-chain USDC allowances for a Safe wallet.
#
# Run on EC2:
#   docker compose exec bot python3 tests/test_allowance.py

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from web3 import Web3

RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
w3 = Web3(Web3.HTTPProvider(RPC))

USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
USDC_E      = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCHANGE       = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE  = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

ALLOWANCE_ABI = [{
    "constant": True,
    "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
    "name": "allowance",
    "outputs": [{"name": "", "type": "uint256"}],
    "type": "function"
}, {
    "constant": True,
    "inputs": [{"name": "_owner", "type": "address"}],
    "name": "balanceOf",
    "outputs": [{"name": "balance", "type": "uint256"}],
    "type": "function"
}]

async def check(safe_address: str):
    safe_cs = Web3.to_checksum_address(safe_address)
    print(f"\nSafe: {safe_cs}")
    print(f"Connected to Polygon: {w3.is_connected()}")

    for token_name, token_addr in [("USDC (native)", USDC_NATIVE), ("USDC.e (bridged)", USDC_E)]:
        contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ALLOWANCE_ABI)
        bal = contract.functions.balanceOf(safe_cs).call()
        bal_usd = bal / 1_000_000
        print(f"\n  {token_name} ({token_addr[:10]}...):")
        print(f"    Balance : ${bal_usd:.4f}")
        for spender_name, spender_addr in [
            ("CTF Exchange     ", CTF_EXCHANGE),
            ("NegRisk Exchange ", NEG_RISK_EXCHANGE),
        ]:
            allowance = contract.functions.allowance(safe_cs, Web3.to_checksum_address(spender_addr)).call()
            status = "[OK]" if allowance > 0 else "[ZERO - needs approve]"
            print(f"    Allowance → {spender_name}: {allowance} {status}")

if __name__ == "__main__":
    import asyncio

    # Try to get safe from DB
    try:
        from bot.services.database import Database
        db = Database()
        import asyncio

        async def main():
            await db.initialize()
            # Get all wallets
            rows = await db.execute_query("SELECT user_id, safe_address, address FROM user_wallets WHERE safe_address IS NOT NULL LIMIT 5")
            if not rows:
                print("No wallets found in DB. Pass a safe address as argument.")
                sys.exit(1)
            for row in rows:
                print(f"\nUser {row['user_id']}: EOA={row['address'][:12]}... Safe={row['safe_address']}")
                await check(row['safe_address'])

        asyncio.run(main())
    except Exception as e:
        if len(sys.argv) < 2:
            print(f"Usage: python3 tests/test_allowance.py <safe_address>")
            print(f"DB access failed: {e}")
            sys.exit(1)
        asyncio.run(check(sys.argv[1]))
