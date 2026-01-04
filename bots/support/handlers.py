"""
handlers.py — Emerald Support Bot (MiniApp-only)

ZIEL:
✅ Alles läuft über die MiniApp (keine Ticket-/Status-Logik im Chat)
✅ Keine Callback-Buttons / keine gruppenbezogenen Einstellungen
✅ Zentrale Registrierung: handlers.register(app) → miniapp.register(app)

Hinweis:
Die eigentliche Logik (Commands/Text-Fallback/WebAppData) liegt in miniapp.py.
"""

import logging
from telegram.ext import Application

logger = logging.getLogger("bot.support.handlers")


def register_handlers(app: Application) -> None:
    """Legacy-kompatibel: registriert die MiniApp-Hub Handler."""
    try:
        # Package-Import
        from .miniapp import register as register_miniapp
    except Exception:
        # Fallback wenn ohne Package-Kontext gestartet
        from miniapp import register as register_miniapp  # type: ignore

    logger.info("Registering Support handlers (MiniApp-only)...")
    register_miniapp(app)
    logger.info("✅ Support handlers registered (MiniApp-only)")


def register(app: Application) -> None:
    """Entry-Point für main bot.py"""
    register_handlers(app)


def register_jobs(app: Application) -> None:
    """Support Bot hat keine Jobs im MiniApp-only Setup."""
    logger.info("Support jobs: none (MiniApp-only)")
