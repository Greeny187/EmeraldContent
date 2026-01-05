# app.py - Emerald Support Bot Main Module (v1.0 - Production Ready)
"""
Emerald Support Bot - Main entry point for telegram bot integration.
Registers handlers, jobs, and initializes database schema.
"""

import logging
import os
import asyncio
from telegram.ext import Application

logger = logging.getLogger("bot.support")

# Import handlers and database
from . import handlers
try:
    from . import database as sql
except ImportError as e:
    logger.warning(f"Could not import sql module: {e}")
    sql = None


async def init_all_schemas():
    """Initialize all database schemas"""
    if sql:
        try:
            res = sql.init_all_schemas()
            if asyncio.iscoroutine(res):
                await res
            logger.info("✅ Database schemas initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize schemas: {e}")
            raise
    else:
        logger.warning("⚠️ SQL module not available, skipping schema initialization")


async def register(app: Application):
    """Register Support Bot handlers into main Application"""
    try:
        await init_all_schemas()
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