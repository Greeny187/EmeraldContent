from telegram.ext import MessageHandler, filters
# robuste Importe aus dem Projekt-Root
try:
    from .handler import route_message
except ImportError:
    import os, sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from bots.crossposter.handler import route_message
try:
    from .miniapp import crossposter_handler
except ImportError:
    import os, sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from .miniapp import crossposter_handler

def register(app):
    flt = (filters.ChatType.GROUPS | filters.ChatType.CHANNEL) & (filters.TEXT | filters.PHOTO | filters.Document.ALL)
    app.add_handler(MessageHandler(flt, route_message))
    app.add_handler(crossposter_handler)
