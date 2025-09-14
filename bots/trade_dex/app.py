import logging
from telegram.ext import CommandHandler

log = logging.getLogger("bot.<name>")

async def _start(update, ctx):
    await update.message.reply_text("Hier entsteht in den nächsten Monaten ein neuer Bot.")

def register(app):
    app.add_handler(CommandHandler("start", _start))

def register_jobs(app):
    pass

def init_schema():
    pass