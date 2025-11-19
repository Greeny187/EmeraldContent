from telegram.ext import Application
from . import handlers, rss, mood, jobs as content_jobs
from . import payment_handlers
import os
import logging
from .miniapp import register_miniapp
from .database import init_all_schemas
try:
    from . import statistic
    from shared import ads
except Exception:
    class _Noop:  # Fallback ohne Seiteneffekte
        def __getattr__(self, _): 
            return lambda *a, **k: None
    statistic, ads = _Noop(), _Noop()

logger = logging.getLogger(__name__)

def register(app):
    if hasattr(statistic, "register_statistics_handlers"):
        statistic.register_statistics_handlers(app)

    if hasattr(handlers, "register_handlers"):
        handlers.register_handlers(app)

    if hasattr(mood, "register_mood"):
        mood.register_mood(app)

    if hasattr(rss, "register_rss"):
        rss.register_rss(app)
    
    # Registriere Payment Handlers (PRO Subscriptions)
    if hasattr(payment_handlers, "register_payment_handlers"):
        payment_handlers.register_payment_handlers(app)

    register_miniapp(app)  # Bot-Befehle registrieren

    # HTTP-Routen für Mini-App registrieren
    try:
        webapp = app.webhook_application()
        if webapp:
            from .miniapp import register_miniapp_routes
            register_miniapp_routes(webapp, app)
            # Registriere auch Payment Webhook Routes
            if hasattr(payment_handlers, "register_payment_routes"):
                payment_handlers.register_payment_routes(webapp)
    except Exception as e:
        logger.warning(f"Could not register miniapp routes: {e}")
    
def register_jobs(app: Application):
    if hasattr(ads, "register_ads_jobs"):
        ads.register_ads_jobs(app)

    # ➕ WICHTIG: Content-Jobs (Rollups, Telethon-Import etc.) registrieren
    if hasattr(content_jobs, "register_jobs"):
        content_jobs.register_jobs(app)

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
    init_all_schemas()