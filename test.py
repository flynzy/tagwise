# verify_encryption.py
"""
Verify that the new encryption key is set up correctly.
"""

import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

async def verify_setup():
    """Verify the encryption setup"""
    from bot.trading.wallet_manager import WalletEncryption
    from bot.services.database import Database, UserWallet
    from bot.config import Config
    from sqlalchemy import select, func
    
    print("=" * 70)
    print("🔍 VERIFYING ENCRYPTION SETUP")
    print("=" * 70)
    
    # Check 1: Environment variable
    key = os.getenv("WALLET_ENCRYPTION_KEY")
    if not key:
        print("❌ WALLET_ENCRYPTION_KEY is not set in .env")
        print("   Add it and try again.")
        return False
    
    print(f"✅ WALLET_ENCRYPTION_KEY is set")
    print(f"   Length: {len(key)} characters")
    print(f"   Preview: {key[:10]}...{key[-10:]}")
    
    # Check 2: Can initialize encryption
    try:
        encryption = WalletEncryption()
        print("✅ WalletEncryption initialized successfully")
    except Exception as e:
        print(f"❌ Failed to initialize encryption: {e}")
        return False
    
    # Check 3: Can encrypt/decrypt
    try:
        test_data = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        encrypted = encryption.encrypt(test_data)
        decrypted = encryption.decrypt(encrypted)
        
        if decrypted == test_data:
            print("✅ Encryption/decryption works correctly")
        else:
            print("❌ Encryption test failed - decrypted data doesn't match")
            return False
    except Exception as e:
        print(f"❌ Encryption test failed: {e}")
        return False
    
    # Check 4: Database is clean
    print("\n🔍 Checking database...")
    db = Database(Config.DATABASE_URL)
    await db.connect()
    
    try:
        async with db.get_session() as session:
            stmt = select(func.count()).select_from(UserWallet)
            result = await session.execute(stmt)
            count = result.scalar()
            
            if count == 0:
                print(f"✅ Database is clean (0 wallets)")
            else:
                print(f"⚠️  Database has {count} existing wallets")
                print(f"   These may not be decryptable with the new key!")
    except Exception as e:
        print(f"❌ Database check failed: {e}")
        return False
    finally:
        await db.close()
    
    print("\n" + "=" * 70)
    print("✅ ALL CHECKS PASSED - SETUP IS CORRECT!")
    print("=" * 70)
    print("\nYou can now:")
    print("  • Start your bot: python main.py")
    print("  • Users can create new wallets with /createwallet")
    print("  • Copy trading will work with new wallets")
    print("=" * 70)
    
    return True

if __name__ == "__main__":
    asyncio.run(verify_setup())