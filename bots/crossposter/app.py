
# bot.py – Minimale Bootstrapping-App für Tests (Polling)
# Für Produktion: Webhook/ASGI einrichten und FastAPI(API) mounten.

import os
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from bots.content.miniapp import crossposter_handler, API
from bots.content.worker import route_message

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "SET_ME")

def build_app():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(crossposter_handler)
    src_filter = (filters.ChatType.GROUPS | filters.ChatType.CHANNEL) & (filters.TEXT | filters.PHOTO | filters.Document.ALL)
    app.add_handler(MessageHandler(src_filter, route_message))
    return app

if __name__ == "__main__":
    app = build_app()
    app.run_polling()
