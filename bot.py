import os
import datetime
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters

from handlers import register_handlers, error_handler
from menu import register_menu
from rss import register_rss
from database import init_db
from logger import setup_logging
from mood import register_mood
from jobs import register_jobs

# --- Setup Logging & Database ---
setup_logging()
init_db()

# --- Config aus ENV ---
BOT_TOKEN    = os.getenv("BOT_TOKEN")
WEBHOOK_URL  = os.getenv("WEBHOOK_URL")   # z.B. https://<app>.herokuapp.com/webhook
PORT         = int(os.getenv("PORT", "8443"))

if not BOT_TOKEN or not WEBHOOK_URL:
    raise ValueError("❌ BOT_TOKEN und WEBHOOK_URL müssen gesetzt sein.")

# --- Bot Application aufbauen ---
app = ApplicationBuilder().token(BOT_TOKEN).build()

# Optional: jedes Update loggen
async def log_update(update: Update, context):
    logging.info(f"📩 Update empfangen: {update}")

# Logging-Level tweak
logging.getLogger("telegram.updatequeue").setLevel(logging.DEBUG)
# Füge das Logging-Handler hinzu
app.add_handler(MessageHandler(filters.ALL, log_update), group=-1)

# Globaler Error-Handler
app.add_error_handler(error_handler)

# Registriere alle Module/Handler
register_handlers(app)
register_rss(app)
register_mood(app)
register_menu(app)
register_jobs(app)

# Uptime in bot_data speichern
app.bot_data["start_time"] = datetime.datetime.now()

# --- Entry Point: run_webhook statt main() ---
if __name__ == "__main__":
    # bindet /webhook an Deinen Heroku-Web-Dyno
    logging.info(f"🎬 Starting bot with webhook {WEBHOOK_URL}")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="/webhook",
        webhook_url=WEBHOOK_URL,
    )