import os
import datetime
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, filters, MessageHandler, ContextTypes
from flask import Flask, request
from handlers import register_handlers, error_handler
from menu import register_menu
from rss import register_rss
from database import init_db
from logger import setup_logging
from mood import register_mood
from jobs import register_jobs

# Logging und DB initialisieren
setup_logging()
init_db()

BOT_TOKEN = os.getenv("BOT_TOKEN")
HEROKU_APP_NAME = os.getenv("HEROKU_APP_NAME")  # z.B. "my-telegram-bot"

if not BOT_TOKEN or not HEROKU_APP_NAME:
    raise ValueError("BOT_TOKEN oder HEROKU_APP_NAME ist nicht gesetzt.")

# Flask-App für Webhook
app = Flask(__name__)

async def log_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"Update angekommen: {update}")

# Telegram-Bot-Instanz global
bot_app = ApplicationBuilder().token(BOT_TOKEN).build()

# Registrierung aller Handler
bot_app.add_handler(MessageHandler(filters.ALL, log_update), group=-1)
bot_app.add_error_handler(error_handler)
register_handlers(bot_app)
register_rss(bot_app)
register_mood(bot_app)
register_menu(bot_app)
register_jobs(bot_app)

# Startzeit
bot_app.bot_data['start_time'] = datetime.datetime.now()

# Endpoint für Telegram
@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    # Bot-Update verarbeiten
    bot_app.update_queue.put(update)
    return 'OK'

if __name__ == "__main__":
    # Starte Flask
    port = int(os.environ.get("PORT", "8443"))
    app.run(host="0.0.0.0", port=port)