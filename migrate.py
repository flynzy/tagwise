# migrate.py
import asyncio
import logging
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# Import Base — try both common paths
try:
    from bot.services.database import Base
    import bot.services.database  # noqa — ensures all models are registered
except ImportError:
    from bot.database import Base
    import bot.database  # noqa

logger.info(f"📋 Registered tables: {list(Base.metadata.tables.keys())}")

COLUMN_MIGRATIONS = [
    "ALTER TABLE copy_trade_settings ADD COLUMN IF NOT EXISTS multibuythreshold INTEGER DEFAULT 2",
    "ALTER TABLE copy_trade_settings ADD COLUMN IF NOT EXISTS multibuysellmode VARCHAR DEFAULT 'any'",
    "ALTER TABLE copy_trade_settings ADD COLUMN IF NOT EXISTS multibuywindow INTEGER DEFAULT 1",
]


async def run_migrations():
    engine = create_async_engine(DATABASE_URL, echo=False)

    # Step 1 — create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Tables created/verified")

    # Step 2 — verify copytradesettings actually exists now
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ))
        tables = [row[0] for row in result]
        logger.info(f"📋 Tables in DB: {tables}")

    if 'copy_trade_settings' not in tables:
        raise RuntimeError("❌ copy_trade_settings table was NOT created — check Base import")

    # Step 3 — add new columns
    async with engine.begin() as conn:
        for sql in COLUMN_MIGRATIONS:
            await conn.execute(text(sql))
            logger.info(f"✅ {sql}")

    await engine.dispose()
    logger.info("🎉 Migration complete")

if __name__ == "__main__":
    asyncio.run(run_migrations())
