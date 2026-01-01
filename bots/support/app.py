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
    await init_all_schemas()  # mit await


# Auto-initialize on import
if __name__ != "__main__":
    import asyncio
    try:
        # Try to initialize synchronously in background
        asyncio.create_task(init_schema())
    except Exception as e:
        logger.warning(f"Could not initialize schema on import: {e}")

