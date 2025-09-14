from telegram.ext import CommandHandler, Application
from . import handlers, menu, rss, mood
import os

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
    if hasattr(handlers, "menu_command"):
        app.add_handler(CommandHandler("menu", handlers.menu_command), group=-3)
    if hasattr(menu, "register_menu"):
        menu.register_menu(app)
    if hasattr(mood, "register_mood"):
        mood.register_mood(app)
    if hasattr(rss, "register_rss"):
        rss.register_rss(app)

    # >>> NEU: Startup-Notify hier sicher einplanen
    async def _notify_startup(ctx):
        # mehrere Fallback-Variablen zulassen
        chat_id = os.getenv("ADMIN_CHAT_ID") or os.getenv("DEVELOPER_CHAT_ID") or os.getenv("STARTUP_NOTIFY_CHAT_ID")
        if chat_id:
            try:
                await ctx.bot.send_message(int(chat_id), "✅ Bot wurde neu gestartet.")
            except Exception:
                pass
    try:
        app.job_queue.run_once(lambda c: app.create_task(_notify_startup(c)), when=2)
    except Exception:
        pass

def init_schema():
    # falls content-spezifische Tabellen/Indizes nötig sind
    pass