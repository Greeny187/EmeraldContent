import os
from telegram.ext import Application
from handlers import register_handlers, error_handler
from menu import register_menu
from rss import register_rss
from database import init_db
from logger import setup_logging
from mood import register_mood
from jobs import register_jobs

# Anfang
setup_logging()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN ist nicht gesetzt.")

def main():
    init_db()
    # Bot-Startzeit
    import datetime
    from logger import setup_logging
    start_time = datetime.datetime.now()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Startzeit in application_data speichern
    app.application_data['start_time'] = start_time

    # Globaler Error-Handler
    app.add_error_handler(error_handler)

    register_handlers(app)
    register_menu(app)
    register_rss(app)
    register_mood(app)

    # JobQueue-Tasks
    register_jobs(app)

    app.run_polling()

if __name__ == "__main__":
    main()
