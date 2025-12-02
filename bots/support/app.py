# app.py - Emerald Support Bot Main Module (v1.0)
"""
Emerald Support Bot - Main entry point for telegram bot integration.
Registers handlers, jobs, and initializes database schema.
"""

import logging
from telegram.ext import Application

logger = logging.getLogger("bot.support")

# Import handlers and database
from . import handlers, sql
from .database import init_all_schemas as db_init_legacy


async def register(app: Application):
    """Register Support Bot handlers into main Application"""
    try:
        handlers.register(app)
        logger.info("✅ Support Bot handlers registered")
    except Exception as e:
        logger.error(f"❌ Failed to register handlers: {e}")
        raise


async def register_jobs(app: Application):
    """Register scheduled jobs (v1.0: empty)"""
    try:
        handlers.register_jobs(app)
        logger.info("✅ Support Bot jobs registered")
    except Exception as e:
        logger.error(f"❌ Failed to register jobs: {e}")


async def init_schema():
    """Initialize database schema"""
    try:
        # Legacy schema init
        db_init_legacy()
        
        # New async schema init
        import os
        import asyncio
        dsn = os.getenv("DATABASE_URL")
        if dsn:
            import psycopg
            async with await psycopg.AsyncConnection.connect(dsn) as conn:
                async with conn.cursor() as cur:
                    # Run migration SQL files
                    with open(os.path.join(os.path.dirname(__file__), "SQL", "001_support_schema.sql")) as f:
                        await cur.execute(f.read())
                    with open(os.path.join(os.path.dirname(__file__), "SQL", "002_support_seed.sql")) as f:
                        await cur.execute(f.read())
                    with open(os.path.join(os.path.dirname(__file__), "SQL", "003_multitenancy.sql")) as f:
                        await cur.execute(f.read())
                await conn.commit()
        
        logger.info("✅ Support Bot schema initialized")
    except Exception as e:
        logger.warning(f"⚠️ Schema init issue (may be normal): {e}")


# Auto-initialize on import
if __name__ != "__main__":
    import asyncio
    try:
        # Try to initialize synchronously
        db_init_legacy()
    except Exception:
        pass
