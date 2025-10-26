from telegram.ext import MessageHandler, filters
from worker import route_message
from miniapp import crossposter_handler

def register(app):
    flt = (filters.ChatType.GROUPS | filters.ChatType.CHANNEL) & (filters.TEXT | filters.PHOTO | filters.Document.ALL)
    app.add_handler(MessageHandler(flt, route_message))
    app.add_handler(crossposter_handler)
