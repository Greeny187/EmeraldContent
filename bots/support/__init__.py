# __init__.py
from telegram.ext import CommandHandler, Application
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import os

WEBAPP_URL = os.getenv("SUPPORT_WEBAPP_URL", "https://.../appsupport.html")

async def start(update, ctx):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔧 Support Mini-App öffnen", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await update.message.reply_text("Hier ist der Emerald Support. Öffne die Mini-App:", reply_markup=kb)

def register(app: Application):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("support", start))
