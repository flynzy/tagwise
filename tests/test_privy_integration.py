# tests/test_privy_integration.py
"""
Live integration test — requires real Privy credentials and network access.
NOT run in CI. Run manually:

    python -m tests.test_privy_integration

Set env vars first:
    PRIVY_APP_ID, PRIVY_APP_SECRET, PRIVY_AUTH_KEY
"""

import os
import asyncio
from dotenv import load_dotenv

load_dotenv()


async def test_privy_create_and_sign():
    """End-to-end: create user + wallet, sign typed data, verify."""

    app_id = os.getenv("PRIVY_APP_ID")
    app_secret = os.getenv("PRIVY_APP_SECRET")
    auth_key = os.getenv("PRIVY_AUTH_KEY")

    if not app_id or not app_secret:
        print("SKIP: PRIVY_APP_ID / PRIVY_APP_SECRET not set")
        return

    from bot.trading.privy_service import PrivyService

    svc = PrivyService(app_id=app_id, app_secret=app_secret, authorization_key=auth_key)

    # 1. Create user + wallet
    print("Creating Privy user + wallet...")
    test_telegram_id = 999999999  # fake test ID
    try:
        result = await svc.create_user_with_wallet(test_telegram_id)
    except Exception as e:
        print(f"FAIL: create_user_with_wallet: {e}")
        return

    print(f"  privy_user_id:   {result['privy_user_id']}")
    print(f"  privy_wallet_id: {result['privy_wallet_id']}")
    print(f"  wallet_address:  {result['wallet_address']}")

    wallet_id = result["privy_wallet_id"]
    address = result["wallet_address"]

    assert wallet_id, "wallet_id is empty"
    assert address and address.startswith("0x"), "address is invalid"
    print("  PASS: user + wallet created")

    # 2. Sign ClobAuth typed data
    print("\nSigning ClobAuth typed data...")
    from bot.trading.privy_signers import privy_sign_clob_auth

    sig = await privy_sign_clob_auth(
        svc, wallet_id, address, chain_id=137, timestamp=1700000000, nonce=0
    )
    assert sig and sig.startswith("0x"), f"Bad signature: {sig}"
    assert len(sig) > 10, "Signature too short"
    print(f"  signature: {sig[:20]}...")
    print("  PASS: ClobAuth signed")

    # 3. Sign a dummy Order typed data
    print("\nSigning Order typed data...")
    from bot.trading.privy_signers import ORDER_TYPES, _order_domain

    order_typed_data = {
        "types": ORDER_TYPES,
        "primary_type": "Order",
        "domain": _order_domain(137, "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
        "message": {
            "salt": "12345",
            "maker": address,
            "signer": address,
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": "1",
            "makerAmount": "1000000",
            "takerAmount": "500000",
            "expiration": "0",
            "nonce": "0",
            "feeRateBps": "0",
            "side": 0,
            "signatureType": 2,
        },
    }

    order_sig = await svc.sign_typed_data(wallet_id, order_typed_data)
    assert order_sig and order_sig.startswith("0x"), f"Bad order sig: {order_sig}"
    print(f"  signature: {order_sig[:20]}...")
    print("  PASS: Order signed")

    # 4. personal_sign (for Safe tx signing)
    print("\nSigning via personal_sign...")
    test_hash = "0x" + "ab" * 32  # 32-byte dummy hash
    personal_sig = await svc.personal_sign(wallet_id, test_hash)
    assert personal_sig and personal_sig.startswith("0x"), f"Bad personal sig: {personal_sig}"
    print(f"  signature: {personal_sig[:20]}...")
    print("  PASS: personal_sign works")

    # 5. L1 headers
    print("\nGenerating L1 headers...")
    from bot.trading.privy_signers import create_privy_level_1_headers
    headers = await create_privy_level_1_headers(svc, wallet_id, address)
    assert headers["POLY_ADDRESS"] == address
    assert headers["POLY_SIGNATURE"].startswith("0x")
    assert int(headers["POLY_TIMESTAMP"]) > 0
    print(f"  POLY_ADDRESS:   {headers['POLY_ADDRESS']}")
    print(f"  POLY_SIGNATURE: {headers['POLY_SIGNATURE'][:20]}...")
    print(f"  POLY_TIMESTAMP: {headers['POLY_TIMESTAMP']}")
    print("  PASS: L1 headers generated")

    print("\n" + "=" * 60)
    print("ALL INTEGRATION TESTS PASSED")
    print("=" * 60)


async def test_wallet_manager_e2e():
    """End-to-end: WalletManager.create_wallet + setup_safe."""

    app_id = os.getenv("PRIVY_APP_ID")
    app_secret = os.getenv("PRIVY_APP_SECRET")
    builder_key = os.getenv("POLYMARKET_BUILDER_API_KEY")

    if not app_id or not app_secret:
        print("SKIP: PRIVY_APP_ID / PRIVY_APP_SECRET not set")
        return
    if not builder_key:
        print("SKIP: POLYMARKET_BUILDER_API_KEY not set (needed for Safe)")
        return

    from bot.services.database import Database
    from bot.config import Config

    db = Database(Config.DATABASE_URL)
    await db.connect()

    try:
        from bot.trading.wallet_manager import WalletManager
        wm = WalletManager(db)

        test_user = 888888888
        print(f"Creating wallet for test user {test_user}...")

        # Clean up any existing test wallet
        await db.delete_user_wallet(test_user)

        result = await wm.create_wallet(test_user)
        print(f"  Result: {result}")

        if result["success"]:
            print(f"  EOA:  {result['address']}")
            print(f"  Safe: {result.get('safe_address')}")

            # Verify wallet was saved with privy fields
            wallet = await wm.get_wallet(test_user)
            assert wallet["privy_wallet_id"], "privy_wallet_id not saved"
            assert wallet["privy_user_id"], "privy_user_id not saved"
            print(f"  privy_wallet_id: {wallet['privy_wallet_id']}")
            print("  PASS: wallet created and saved")

            # Setup Safe
            print("\nSetting up Safe...")
            setup = await wm.setup_safe(test_user)
            print(f"  Setup result: {setup}")

            if setup["success"]:
                print("  PASS: Safe deployed + allowances set")

                # Check status
                status = await wm.get_wallet_status(test_user)
                print(f"  Status: {status}")
            else:
                print(f"  WARN: Safe setup failed: {setup.get('error')}")

            # Cleanup
            print("\nCleaning up test wallet...")
            await db.delete_user_wallet(test_user)
            print("  Done")
        else:
            print(f"  FAIL: {result['error']}")

    finally:
        await db.close()


if __name__ == "__main__":
    print("=" * 60)
    print("PRIVY INTEGRATION TESTS (requires real credentials)")
    print("=" * 60)

    print("\n--- Test 1: Privy Create + Sign ---\n")
    asyncio.run(test_privy_create_and_sign())

    print("\n--- Test 2: WalletManager E2E ---\n")
    asyncio.run(test_wallet_manager_e2e())
