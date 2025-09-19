import os
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from bots.crossposter.worker import route_message  # use local worker

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "SET_ME")

def build_app():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    # source filter: groups/channels with text or media
    src_filter = (filters.ChatType.GROUPS | filters.ChatType.CHANNEL) & (filters.TEXT | filters.PHOTO | filters.Document.ALL)
    app.add_handler(MessageHandler(src_filter, route_message))
    return app

if __name__ == "__main__":
    app = build_app()
    app.run_polling()
