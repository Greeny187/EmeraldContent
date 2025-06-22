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


# Anfang

setup_logging()
init_db()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN ist nicht gesetzt.")

async def log_update(update, context):
    logging.info(f"Update angekommen: {update}")

def main():
    
    # Startzeit merken
    start_time = datetime.datetime.now()
    
    #Botstart
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    logging.getLogger("telegram.updatequeue").setLevel(logging.DEBUG)
    app.add_handler(MessageHandler(filters.ALL, log_update),group=-1)

    # Globaler Error-Handler
    app.add_error_handler(error_handler)

    # Handlerregistrierung
    register_handlers(app)
    register_rss(app)
    register_mood(app)
    register_menu(app)
    register_jobs(app)

    app.bot_data['start_time'] = start_time

    app.run_polling(allowed_updates=["chat_member", "my_chat_member", "message", "callback_query"])

if __name__ == "__main__":
    main()
