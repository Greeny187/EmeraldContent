import os
import datetime
import logging
from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from telegram.ext import get_running_loop

from handlers import register_handlers, error_handler
from menu import register_menu
from rss import register_rss
from database import init_db
from logger import setup_logging
from mood import register_mood
from jobs import register_jobs

# --- Setup ---
setup_logging()
init_db()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # z. B. https://deinprojekt.herokuapp.com/webhook
PORT = int(os.environ.get("PORT", 8443))

if not BOT_TOKEN or not WEBHOOK_URL:
    raise ValueError("❌ BOT_TOKEN oder WEBHOOK_URL ist nicht gesetzt.")

# ⬇ globales app-Objekt
app = None

# Optionales Logging jedes Updates
async def log_update(update, context):
    logging.info(f"📩 Update empfangen: {update}")

# ⬇ Webhook-Endpunkt
async def handle(request):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="OK")

# ⬇ Main-Funktion für Webhook-Betrieb
async def main():
    global app
    start_time = datetime.datetime.now()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    logging.getLogger("telegram.updatequeue").setLevel(logging.DEBUG)
    app.add_handler(MessageHandler(filters.ALL, log_update), group=-1)
    app.add_error_handler(error_handler)

    # ⬇ Handler & Module
    register_handlers(app)
    register_rss(app)
    register_mood(app)
    register_menu(app)
    register_jobs(app)

    app.bot_data['start_time'] = start_time

    # ⬇ Webhook bei Telegram registrieren
    await app.bot.set_webhook(WEBHOOK_URL)

    # ⬇ aiohttp Webserver starten
    web_app = web.Application()
    web_app.router.add_post("/webhook", handle)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logging.info(f"🚀 Webhook läuft auf {WEBHOOK_URL} (Port {PORT})")

    # ⬇ Endlos warten
    await get_running_loop().create_future()

# ⬇ Entry Point
if __name__ == "__main__":
    import asyncio
    asyncio.run(main())