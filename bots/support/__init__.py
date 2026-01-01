# __init__.py - Support Bot Package
"""
Emerald Support Bot - Complete support ticket system for Telegram
Provides handlers, API endpoints, and WebApp integration
"""

__version__ = "1.0.0"
__author__ = "Emerald Community"

# Main entry points
from . import app as support_app
from . import handlers
from . import config

# Expose main functions
async def register(telegram_app):
    """Register support bot handlers with main telegram app"""
    return await support_app.register(telegram_app)

async def register_jobs(telegram_app):
    """Register support bot jobs with main telegram app"""
    return await support_app.register_jobs(telegram_app)

async def init_schema():
    """Initialize database schema"""
    return await support_app.init_schema()

__all__ = [
    "support_app",
    "handlers",
    "config",
    "register",
    "register_jobs",
    "init_schema",
]
