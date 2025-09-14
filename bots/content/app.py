from telegram.ext import CommandHandler
from . import handlers, menu, rss, mood
from shared import statistic, ads

def register(app):
    if hasattr(statistic, "register_statistics_handlers"):
        statistic.register_statistics_handlers(app)

    if hasattr(handlers, "register_handlers"):
        handlers.register_handlers(app)

    # Dein echtes /menu an den Original-Handler binden (kein Fallback)
    if hasattr(handlers, "menu_command"):
        app.add_handler(CommandHandler("menu", handlers.menu_command), group=-3)

    if hasattr(menu, "register_menu"):
        menu.register_menu(app)   # registriert Callback/Reply-Handler

    if hasattr(mood, "register_mood"):
        mood.register_mood(app)

    if hasattr(rss, "register_rss"):
        rss.register_rss(app)

def register_jobs(app):
    if hasattr(ads, "register_ads_jobs"):
        ads.register_ads_jobs(app)

def init_schema():
    # falls content-spezifische Tabellen/Indizes nötig sind
    pass