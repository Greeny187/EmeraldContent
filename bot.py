import os
import datetime
import logging
import statistic
import asyncio
from telegram.ext import ApplicationBuilder, filters, MessageHandler
from telethon import TelegramClient
from handlers import register_handlers, error_handler
from menu import register_menu
from rss import register_rss
from database import init_db
from logger import setup_logging
from mood import register_mood
from jobs import register_jobs

# 1. Telegram-API-Zugangsdaten prüfen
API_ID    = os.getenv("TG_API_ID")
API_HASH  = os.getenv("TG_API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not all([API_ID, API_HASH, BOT_TOKEN]):
    raise RuntimeError("TG_API_ID, TG_API_HASH und BOT_TOKEN müssen als Env-Vars gesetzt sein!")

# 1) Client erzeugen
telethon_client = TelegramClient('bot_session', int(API_ID), API_HASH)

# 2) Bot-Token-basiert starten – kein input() mehr!
async def start_telethon():
    await telethon_client.start(bot_token=BOT_TOKEN)

# 3) Synchronously ensure the client is started before handlers
asyncio.get_event_loop().run_until_complete(start_telethon())

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN ist nicht gesetzt.")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL ist nicht gesetzt.")

async def log_update(update, context):
    logging.info(f"Update angekommen: {update}")

def main():

    setup_logging()
    init_db()
    statistic.init_stats_db()
    BOT_TOKEN   = os.getenv("BOT_TOKEN")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    PORT        = int(os.getenv("PORT", 8443))
    PORT = int(os.getenv("PORT", 8443))

    # 1) Telethon starten (in eigenem Loop)
    asyncio.get_event_loop().run_until_complete(start_telethon())

    app = (ApplicationBuilder().token(BOT_TOKEN).connection_pool_size(50).pool_timeout(20.0).concurrent_updates(20).build())

    logging.getLogger("telegram.updatequeue").setLevel(logging.DEBUG)

    # Deine Handler und Error-Handler registrieren
    app.add_error_handler(error_handler)
    app.add_handler(MessageHandler(filters.ALL, log_update), group=-1)
    register_handlers(app)
    statistic.register_statistics_handlers(app)
    register_rss(app)
    register_mood(app)
    register_menu(app)
    register_jobs(app)

    # Startzeit merken (optional)
    app.bot_data['start_time'] = datetime.datetime.now()

    # Webhook starten
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=f"/webhook/{BOT_TOKEN}",
        webhook_url=WEBHOOK_URL,
        drop_pending_updates=True,
        max_connections=40
    )

if __name__ == "__main__":
    main()