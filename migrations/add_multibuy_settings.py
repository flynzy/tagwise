import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text  # ✅ ADD THIS LINE
from bot.services.database import Database
from bot.config import Config

async def migrate():
    db = Database(Config.DATABASE_URL)
    await db.connect()
    
    async with db.get_session() as session:
        await session.execute(text("""
            ALTER TABLE user_subscriptions 
            ADD COLUMN IF NOT EXISTS multibuy_enabled BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS multibuy_min_wallets INTEGER DEFAULT 2,
            ADD COLUMN IF NOT EXISTS multibuy_min_amount DOUBLE PRECISION DEFAULT 0.0;
        """))
        await session.commit()
    
    await db.close()
    print("✅ Migration complete")

if __name__ == "__main__":
    asyncio.run(migrate())