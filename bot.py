import os
import asyncio
import logging
from typing import Dict
from importlib import import_module

from aiohttp import web
from telegram import Update
from telegram.ext import Application, PicklePersistence

DEFAULT_BOT_NAMES = ["content", "trade_api", "trade_dex", "crossposter", "learning", "support"]
APP_BASE_URL = os.getenv("APP_BASE_URL")
PORT = int(os.getenv("PORT", "8443"))
DEVELOPER_CHAT_ID = os.getenv("DEVELOPER_CHAT_ID", "5114518219")

def load_bots_env():
    bots = []
    for idx, name in enumerate(DEFAULT_BOT_NAMES, start=1):
        key = os.getenv(f"BOT{idx}_KEY", name)
        token = os.getenv(f"BOT{idx}_TOKEN")
        username = os.getenv(f"BOT{idx}_USERNAME")  # optional
        bots.append({
            "name": name,
            "route_key": key,
            "token": token,
            "username": username,
            "index": idx
        })
    return bots

BOTS = load_bots_env()

def mask(value: str, show: int = 6):
    if not value:
        return None
    if len(value) <= show:
        return "*" * len(value)
    return "*" * (len(value) - show) + value[-show:]

def sanitize_env() -> Dict[str, str]:
    env = {}
    sensitive_markers = ("TOKEN","SECRET","KEY","PASS","PWD","HASH","API","ACCESS","PRIVATE")
    whitelist_prefixes = ("APP_", "PORT", "BOT", "DATABASE_URL", "REDIS_URL", "TG_", "TELETHON", "PAYPAL_", "COINBASE_", "BINANCE_", "BYBIT_", "REVOLUT_", "OPENAI_", "ANTHROPIC_", "GEMINI_")
    for k, v in os.environ.items():
        if not any(k.startswith(p) for p in whitelist_prefixes):
            continue
        if any(m in k for m in sensitive_markers):
            env[k] = mask(v or "", 6)
        else:
            env[k] = v
    return env

APPLICATIONS: Dict[str, Application] = {}
ROUTEKEY_TO_NAME: Dict[str, str] = {}
WEBHOOK_URLS: Dict[str, str] = {}

async def build_application(bot_cfg: Dict, is_primary: bool) -> Application:
    name = bot_cfg["name"]
    route_key = bot_cfg["route_key"]
    token = bot_cfg["token"]

    persistence = PicklePersistence(filepath=f"state_{route_key}.pickle")
    app_builder = Application.builder().token(token).arbitrary_callback_data(True).persistence(persistence)

    request = None
    if name == "content":
        try:
            req_mod = import_module("bots.content")
            request = getattr(req_mod, "create_request_with_increased_pool")()
        except Exception:
            request = None
    if request is not None:
        app_builder = app_builder.request(request)

    app = app_builder.build()

    app.bot_data['bot_key'] = route_key        # z.B. "content", "trade_api", ...
    app.bot_data['bot_name'] = name            # logischer Name
    app.bot_data['bot_index'] = bot_cfg["index"]
    
    async def _on_error(update, context):
        logging.exception("Unhandled error", exc_info=context.error)
    app.add_error_handler(_on_error)

    if is_primary:
        try:
            import_module("bots.content").setup_logging()
        except Exception:
            pass
        try:
            import_module("bots.content").init_all_schemas()
        except Exception as e:
            logging.warning(f"init_all_schemas() skipped: {e}")
        try:
            import_module("bots.content").init_ads_schema()
        except Exception:
            pass

    pkg = import_module(f"bots.{name}")
    if hasattr(pkg, "register"):
        pkg.register(app)
    if hasattr(pkg, "register_jobs"):
        pkg.register_jobs(app)

    if is_primary:
        try:
            tc, starter = import_module("bots.content").get_telethon_client_and_starter()
        except Exception:
            tc, starter = None, None
        if tc is not None:
            app.bot_data['telethon_client'] = tc
        if starter is not None:
            try:
                await starter()
            except Exception as e:
                logging.warning(f"Telethon start failed: {e}")

    async def _post_init(application: Application) -> None:
        try:
            await application.bot.send_message(chat_id=DEVELOPER_CHAT_ID,
                text=f"🤖 Bot '{name}' ({route_key}) ist online.")
        except Exception:
            pass
    app.post_init = _post_init

    return app

async def webhook_handler(request: web.Request):
    route_key = request.match_info.get("route_key")
    app = APPLICATIONS.get(route_key)
    if not app:
        return web.Response(status=404, text="Unknown bot route key.")
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON")

    update = Update.de_json(data=data, bot=app.bot)
    await app.process_update(update)
    return web.json_response({"ok": True})

async def health_handler(_: web.Request):
    return web.json_response({
        "status": "ok",
        "bots": list(APPLICATIONS.keys()),
        "webhook_urls": WEBHOOK_URLS
    })

async def env_handler(_: web.Request):
    return web.json_response(sanitize_env())

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    if not BOTS or not BOTS[0]["token"]:
        raise RuntimeError("BOT1_TOKEN (Emerald Content Bot) is required.")

    if not APP_BASE_URL:
        raise RuntimeError("APP_BASE_URL must be set (e.g. https://<app>.herokuapp.com)")

    # Build + start apps
    for idx, cfg in enumerate(BOTS):
        if not cfg["token"]:
            continue
        app = await build_application(cfg, is_primary=(idx == 0))
        await app.initialize()
        await app.start()
        APPLICATIONS[cfg["route_key"]] = app
        ROUTEKEY_TO_NAME[cfg["route_key"]] = cfg["name"]
        WEBHOOK_URLS[cfg["name"]] = f"{APP_BASE_URL}/webhook/{cfg['route_key']}"

    if not APPLICATIONS:
        raise RuntimeError("No bots configured (no tokens found).")

    webapp = web.Application()
    webapp.router.add_get("/health", health_handler)
    webapp.router.add_get("/env", env_handler)
    webapp.router.add_post("/webhook/{route_key}", webhook_handler)

    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    logging.info(f"Webhook server listening on 0.0.0.0:{PORT}")
    await site.start()

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        for app in APPLICATIONS.values():
            await app.stop()
            await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())