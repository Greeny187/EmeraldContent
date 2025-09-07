from telegram.ext import CommandHandler, Application

async def start(update, ctx):
        await update.message.reply_text("Hier entsteht in den nächsten Monaten ein neuer Bot.")

def register(app: Application):
    app.add_handler(CommandHandler("start", start))