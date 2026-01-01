# app.py - Emerald Support Bot Main Module (v1.0 - Production Ready)
"""
Emerald Support Bot - Main entry point for telegram bot integration.
Registers handlers, jobs, and initializes database schema.
"""

import logging
import os
from telegram.ext import Application

logger = logging.getLogger("bot.support")

# Import handlers and database
from . import handlers
from . import sql


async def register(app: Application):
    """Register Support Bot handlers into main Application"""
    try:
        handlers.register(app)
        logger.info("✅ Support Bot handlers registered")
    except Exception as e:
        logger.error(f"❌ Failed to register handlers: {e}")
        raise


async def register_jobs(app: Application):
    """Register scheduled jobs"""
    try:
        handlers.register_jobs(app)
        logger.info("✅ Support Bot jobs registered")
    except Exception as e:
        logger.error(f"❌ Failed to register jobs: {e}")


async def init_schema():
    """Initialize database schema"""
    try:
        import asyncio
        
        # Initialize pool
        await sql.init_pool()
        
        # Run migration SQL files
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            logger.warning("DATABASE_URL not set, skipping schema init")
            return
        
        sql_dir = os.path.join(os.path.dirname(__file__), "SQL")
        
        # Import psycopg to run migrations
        import psycopg
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                # Run each migration file in order
                for sql_file in ["001_support_schema.sql", "002_support_seed.sql", "003_multitenancy.sql"]:
                    sql_path = os.path.join(sql_dir, sql_file)
                    if os.path.exists(sql_path):
                        with open(sql_path) as f:
                            sql_content = f.read()
                            # Split by semicolon and execute each statement
                            for statement in sql_content.split(';'):
                                statement = statement.strip()
                                if statement:
                                    try:
                                        await cur.execute(statement)
                                    except Exception as e:
                                        logger.warning(f"Error executing SQL from {sql_file}: {e}")
            await conn.commit()
        
        logger.info("✅ Support Bot schema initialized")
    except FileNotFoundError:
        logger.warning("⚠️ SQL migration files not found (may be normal)")
    except Exception as e:
        logger.warning(f"⚠️ Schema init issue (may be normal): {e}")


# Auto-initialize on import
if __name__ != "__main__":
    import asyncio
    try:
        # Try to initialize synchronously in background
        asyncio.create_task(init_schema())
    except Exception as e:
        logger.warning(f"Could not initialize schema on import: {e}")

