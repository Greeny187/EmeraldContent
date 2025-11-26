"""Affiliate Bot"""

from telegram.ext import Application

from . import handlers as cmd
from .database import init_all_schemas
from .miniapp import register_miniapp


def register(app: Application):
    """Register handlers"""
    cmd.register_handlers(app)


def register_jobs(app: Application):
    """Register background jobs"""
    pass


def init_schema():
    """Initialize database schema"""
    init_all_schemas()


async def register_miniapp_handler(app):
    """Register miniapp routes"""
    if hasattr(app, 'webapp'):
        await register_miniapp(app.webapp)
