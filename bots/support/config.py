# config.py - Support Bot Configuration (v1.0)
"""
Centralized configuration for Support Bot
"""

import os
from typing import Final

# ============ Environment Variables ============

# Database
DATABASE_URL: Final = os.getenv("DATABASE_URL", "postgres://user:pass@localhost/emerald_support")

# Telegram Bot
BOT_TOKEN: Final = os.getenv("BOT6_TOKEN") or os.getenv("BOT_TOKEN", "")
WEBHOOK_URL: Final = os.getenv("WEBHOOK_URL", "https://emerald-bots.herokuapp.com")
WEBHOOK_PATH: Final = "/webhook/support"

# WebApp
SUPPORT_WEBAPP_URL: Final = os.getenv(
    "SUPPORT_WEBAPP_URL",
    "https://greeny187.github.io/EmeraldContentBots/miniapp/appsupport.html"
)

# API
API_HOST: Final = os.getenv("API_HOST", "0.0.0.0")
API_PORT: Final = int(os.getenv("API_PORT", 8000))
API_PREFIX: Final = "/api/support"

# Logging
LOG_LEVEL: Final = os.getenv("LOG_LEVEL", "INFO")

# ============ Settings ============

# Ticket Categories
TICKET_CATEGORIES = ["allgemein", "technik", "zahlungen", "konto", "feedback"]

# Ticket Status
TICKET_STATUSES = ["neu", "in_bearbeitung", "warten", "geloest", "archiv"]

# SLA (Service Level Agreement) in hours
SLA_SETTINGS = {
    "critical": 1,    # < 1 hour
    "high": 4,        # < 4 hours
    "normal": 24,     # < 24 hours
    "low": 72,        # < 72 hours
}

# Default pagination
DEFAULT_LIMIT = 30
MAX_LIMIT = 100

# Message limits
MESSAGE_MIN_LENGTH = 1
MESSAGE_MAX_LENGTH = 4000

SUBJECT_MIN_LENGTH = 4
SUBJECT_MAX_LENGTH = 140

BODY_MIN_LENGTH = 10
BODY_MAX_LENGTH = 4000

# KB Search
KB_SEARCH_MIN_LENGTH = 2
KB_SEARCH_MAX_RESULTS = 20

# ============ Feature Flags ============

FEATURES = {
    "tickets_enabled": True,
    "kb_search_enabled": True,
    "group_settings_enabled": True,
    "stats_enabled": True,
    "ai_faq_enabled": True,
    "ai_rss_enabled": True,
    "mood_question_enabled": True,
    "daily_stats_enabled": True,
}

# ============ Default Messages ============

MESSAGES = {
    "welcome": "🎫 **Emerald Support**\n\nWillkommen! Hier kannst du Support-Tickets erstellen und verwalten.",
    "ticket_created": "✅ Ticket #{ticket_id} erstellt.",
    "ticket_closed": "✅ Ticket #{ticket_id} geschlossen.",
    "error": "❌ Ein Fehler ist aufgetreten.",
    "permission_denied": "❌ Du hast keine Berechtigung für diese Aktion.",
    "not_found": "❌ Nicht gefunden.",
}

# ============ Validation ============

def validate_config() -> bool:
    """Validate that all required configuration is set"""
    required = [
        ("DATABASE_URL", DATABASE_URL),
        ("BOT_TOKEN", BOT_TOKEN),
    ]
    
    missing = [name for name, value in required if not value]
    
    if missing:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Missing required configuration: {', '.join(missing)}")
        return False
    
    return True


# ============ Logging Configuration ============

def setup_logging():
    """Setup logging configuration"""
    import logging
    
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    return logging.getLogger(__name__)


if __name__ == "__main__":
    # Validate on module load
    if validate_config():
        print("✅ Configuration valid")
    else:
        print("❌ Configuration invalid")
