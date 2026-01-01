"""Emerald Academy Learning Bot"""

from telegram.ext import CommandHandler, Application
import logging

logger = logging.getLogger(__name__)

async def start(update, ctx):
    """Welcome message"""
    await update.message.reply_text(
        "📚 **Emerald Academy**\n\n"
        "Willkommen zum Learning Bot!\n"
        "Nutze /help für alle Befehle."
    )

def register(app: Application):
    """Register Learning Bot"""
    try:
        from . import handlers, miniapp, database
        
        # Initialize database
        database.init_all_schemas()
        
        # Register handlers
        if hasattr(handlers, 'register_handlers'):
            handlers.register_handlers(app)
            logger.info("✅ Learning handlers registered")
        
        # Register miniapp
        if hasattr(miniapp, 'register_miniapp'):
            miniapp.register_miniapp(app)
            logger.info("✅ Learning miniapp registered")
        
    except ImportError as e:
        logger.error(f"❌ Failed to import learning modules: {e}")
    except Exception as e:
        logger.error(f"❌ Learning registration error: {e}")


__all__ = ['register', 'start']