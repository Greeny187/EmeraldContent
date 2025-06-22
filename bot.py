import os
import datetime
import logging
from telegram.ext import ApplicationBuilder, filters, MessageHandler
from handlers import register_handlers, error_handler
from menu import register_menu
from rss import register_rss
from database import init_db
from logger import setup_logging
from mood import register_mood
from jobs import register_jobs

BOT_TOKEN   = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN ist nicht gesetzt.")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL ist nicht gesetzt.")

async def log_update(update, context):
    logging.info(f"Update angekommen: {update}")

async def on_startup(app):
    await app.bot.set_webhook(
        webhook_url=WEBHOOK_URL,
        drop_pending_updates=True
    )
    print(f"✅ Webhook gesetzt: {WEBHOOK_URL}")

def main():
    setup_logging()
    init_db()

    PORT = int(os.getenv("PORT", 8443))
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    logging.getLogger("telegram.updatequeue").setLevel(logging.DEBUG)

    # Deine Handler und Error-Handler registrieren
    app.add_error_handler(error_handler)
    app.add_handler(MessageHandler(filters.ALL, log_update), group=-1)
    register_handlers(app)
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
        on_startup=on_startup
    )

if __name__ == "__main__":
    main()