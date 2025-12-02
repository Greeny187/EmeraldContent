import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler
from bots.content import app

logger = logging.getLogger(__name__)

async def debug_all_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    logger.warning("DEBUG CALLBACK: data=%r chat_id=%s user_id=%s", q.data, q.message.chat_id, q.from_user.id)
    # kleine visuelle Rückmeldung im Chat
    try:
        await q.answer(f"DEBUG: {q.data}", show_alert=False)
    except Exception:
        pass
    
def register_debug(app):
    # ganz früh, *vor* captcha callback:
    app.add_handler(CallbackQueryHandler(debug_all_callbacks), group=-5)