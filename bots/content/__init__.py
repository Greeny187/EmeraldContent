from telegram.ext import CommandHandler
from . import menu, handlers, rss, mood   # <- relative Imports!
from shared import statistic, ads
import logging
logger = logging.getLogger("shared.content")

def _imp_rel(mod):
    try:
        return __import__(f".{mod}", fromlist=["*"])
    except Exception:
        return None

def _imp_shared(mod):
    try:
        return __import__(f"shared.{mod}", fromlist=["*"])
    except Exception:
        return None

# Content-Module (relativ zu bots/content)
_mods = {
    "handlers": _imp_rel("handlers"),
    "menu": _imp_rel("menu"),
    "rss": _imp_rel("rss"),
    "mood": _imp_rel("mood"),
    "access": _imp_rel("access"),
    "ai_core": _imp_rel("ai_core"),
    "utils": _imp_rel("utils"),
    "user_manual": _imp_rel("user_manual"),
    "patchnotes": _imp_rel("patchnotes"),
    "import_group_once": _imp_rel("import_group_once"),
    "import_members": _imp_rel("import_members"),
}

# Shared-Module (aus shared/)
_shared = {
    "db": _imp_shared("database"),
    "logger": _imp_shared("logger"),
    "statistic": _imp_shared("statistic"),
    "jobs": _imp_shared("jobs"),
    "ads": _imp_shared("ads"),
    "payments": _imp_shared("payments"),
    "telethon": _imp_shared("telethon_client"),
    "maintenance": _imp_shared("maintenance"),
    "translator": _imp_shared("translator"),
    "devmenu": _imp_shared("devmenu"),
}

# Root request_config (bleibt im Projekt-Root)
try:
    import request_config as _reqcfg
except Exception:
    _reqcfg = None

# kurzes Inventar ins Log (hilft sofort beim Debuggen)
for k, v in {**_mods, **{f"shared.{k}": v for k, v in _shared.items()}}.items():
    logger.info("content.mod %-18s -> %s", k, "ok" if v else "missing")

def setup_logging():
    lg = _mods.get('logger')
    if lg and hasattr(lg, 'setup_logging'):
        lg.setup_logging()

def _safe_register(mod, func_name, app, label):
    try:
        if mod and hasattr(mod, func_name):
            getattr(mod, func_name)(app)
            logger.info("content.register %-12s -> OK", label)
        else:
            logger.warning("content.register %-12s -> SKIP (missing)", label)
    except Exception:
        logger.exception("content.register %-12s -> FAILED", label)

def create_request_with_increased_pool():
    if _reqcfg and hasattr(_reqcfg, "create_request_with_increased_pool"):
        return _reqcfg.create_request_with_increased_pool()
    return None

def init_all_schemas():
    db = _shared.get('db')
    if db and hasattr(db, 'init_all_schemas'):
        db.init_all_schemas()

def init_ads_schema():
    ads = _mods.get('ads')
    if ads and hasattr(ads, 'init_ads_schema'):
        ads.init_ads_schema()

def register(app):
    # Reihenfolge: shared zuerst, dann content
    if hasattr(statistic, "register_statistics_handlers"):
        statistic.register_statistics_handlers(app)

    if hasattr(handlers, "register_handlers"):
        handlers.register_handlers(app)

    # 👉 Dein /menu direkt mit deinem echten Handler verbinden
    if hasattr(handlers, "menu_command"):
        app.add_handler(CommandHandler("menu", handlers.menu_command), group=-3)
        logger.info("content: /menu CommandHandler registriert (group=-3)")
    else:
        logger.warning("content: handlers.menu_command fehlt")

    if hasattr(mood, "register_mood"):
        mood.register_mood(app)

    if hasattr(menu, "register_menu"):
        menu.register_menu(app)
        logger.info("content: register_menu() installiert")
    else:
        logger.warning("content: menu.register_menu fehlt")

    if hasattr(rss, "register_rss"):
        rss.register_rss(app)

def register_jobs(app):
    if hasattr(ads, "register_ads_jobs"):
        ads.register_ads_jobs(app)
    if _mods.get('jobs') and hasattr(_mods['jobs'], 'register_jobs'):
        _mods['jobs'].register_jobs(app)
    if _shared.get("jobs") and hasattr(_shared["jobs"], "register_jobs"):
        _shared["jobs"].register_jobs(app)
    if _shared.get("ads") and hasattr(_shared["ads"], "register_ads_jobs"):
        _shared["ads"].register_ads_jobs(app)

def get_telethon_client_and_starter():
    t = _shared.get("telethon")
    if not t:
        return None, None
    tc = getattr(t, "telethon_client", None)
    starter = getattr(t, "start_telethon", None)
    return tc, starter

