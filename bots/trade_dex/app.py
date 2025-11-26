"""Trade DEX Bot - Application Setup"""

import logging
import os
from telegram.ext import Application

logger = logging.getLogger(__name__)

try:
    from . import handlers
    from . import miniapp
    from . import database
except ImportError as e:
    logger.error(f"Failed to import modules: {e}")
    handlers = miniapp = database = None


def register(app: Application):
    """Register all DEX handlers"""
    if handlers and hasattr(handlers, "register_handlers"):
        handlers.register_handlers(app)
        logger.info("DEX handlers registered")
    
    if miniapp and hasattr(miniapp, "register_miniapp"):
        miniapp.register_miniapp(app)
        logger.info("DEX miniapp registered")
    
    try:
        webapp = app.webhook_application()
        if webapp and miniapp and hasattr(miniapp, "register_miniapp_routes"):
            miniapp.register_miniapp_routes(webapp, app)
            logger.info("DEX miniapp routes registered")
    except Exception as e:
        logger.warning(f"Could not register miniapp routes: {e}")


def register_jobs(app: Application):
    """Register background jobs"""
    pass


def init_schema():
    """Initialize database schema"""
    if database and hasattr(database, "init_all_schemas"):
        database.init_all_schemas()
        logger.info("DEX database schema initialized")
