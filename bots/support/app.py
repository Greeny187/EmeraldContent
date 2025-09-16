# app.py
import logging
from telegram.ext import CommandHandler
log = logging.getLogger("bot.support")

async def _start(update, ctx):
    await update.message.reply_text("Hier ist der Emerald Support. Nutze /support für die Mini-App.")

def register(app):
    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CommandHandler("support", _start))

def register_jobs(app): pass
def init_schema(): pass
