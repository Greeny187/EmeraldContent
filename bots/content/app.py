from telegram.ext import CommandHandler, Application
from . import handlers, menu, rss, mood
import os
from .miniapp import register_miniapp
try:
    from shared import statistic, ads
except Exception:
    class _Noop:  # Fallback ohne Seiteneffekte
        def __getattr__(self, _): 
            return lambda *a, **k: None
    statistic, ads = _Noop(), _Noop()


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

    register_miniapp(app)  # << Mini-App einhängen
    
def register_jobs(app: Application):
    if hasattr(ads, "register_ads_jobs"):
        ads.register_ads_jobs(app)

    async def _notify_startup(ctx):
        chat_id = os.getenv("ADMIN_CHAT_ID")
        if chat_id:
            try:
                await ctx.bot.send_message(int(chat_id), "✅ Bot wurde neu gestartet.")
            except Exception as e:
                # optional loggen
                pass

    # einmalig 2s nach Start
    app.job_queue.run_once(lambda c: app.create_task(_notify_startup(c)), when=2)

def init_schema():
    # falls content-spezifische Tabellen/Indizes nötig sind
    pass