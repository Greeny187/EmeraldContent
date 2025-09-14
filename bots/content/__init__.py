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
    # shared statistic/devmenu zuerst (falls vorhanden)
    if _shared.get("statistic") and hasattr(_shared["statistic"], "register_statistics_handlers"):
        _shared["statistic"].register_statistics_handlers(app)
    if _shared.get("devmenu") and hasattr(_shared["devmenu"], "register_dev_handlers"):
        _shared["devmenu"].register_dev_handlers(app)
    # content handlers/menu/rss/mood
    if _mods.get('handlers') and hasattr(_mods['handlers'], 'register_handlers'):
        _mods['handlers'].register_handlers(app)
    if _mods.get('mood') and hasattr(_mods['mood'], 'register_mood'):
        _mods['mood'].register_mood(app)
    if _mods.get('menu') and hasattr(_mods['menu'], 'register_menu'):
        _mods['menu'].register_menu(app)
    if _mods.get('rss') and hasattr(_mods['rss'], 'register_rss'):
        _mods['rss'].register_rss(app)
    if _shared.get('ads') and hasattr(_shared['ads'], 'register_ads'):
        _shared['ads'].register_ads(app)

    # --- Fallback & Diagnose ---
    try:
        from telegram.ext import CommandHandler, MessageHandler, filters

        async def _fallback_start(update, ctx):
            await update.message.reply_text("âœ… Emerald Content Bot ist online. (Fallback)")

        async def _ping(update, ctx):
            await update.message.reply_text("pong")

        async def _echo(update, ctx):
            if update.message and update.message.text:
                await update.message.reply_text(f"ðŸ‘€ {update.message.text}")

        app.add_handler(CommandHandler("start", _fallback_start), group=99)
        app.add_handler(CommandHandler("ping", _ping), group=99)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _echo), group=99)
    except Exception as e:
        logger.warning("Fallback/Diagnose-Handler nicht registriert: %s", e)
 

def register_jobs(app):
    if _mods.get('jobs') and hasattr(_mods['jobs'], 'register_jobs'):
        _mods['jobs'].register_jobs(app)
    if _mods.get('ads') and hasattr(_mods['ads'], 'register_ads_jobs'):
        _mods['ads'].register_ads_jobs(app)
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
