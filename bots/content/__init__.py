from .app import register, register_jobs, init_schema  # re-export

# ---- Backwards-Compat, weil bot.py diese Symbole erwartet ----
def setup_logging():
    try:
        from shared.logger import setup_logging as _s
        _s()
    except Exception:
        pass

def init_all_schemas():
    try:
        from shared.database import init_all_schemas as _init
        _init()
    except Exception:
        pass

def init_ads_schema():
    try:
        from shared.ads import init_ads_schema as _ads
        _ads()
    except Exception:
        pass

def get_telethon_client_and_starter():
    # optional: nur wenn du Telethon nutzt
    try:
        from shared.telethon_client import telethon_client, start_telethon
        return telethon_client, start_telethon
    except Exception:
        return None, None