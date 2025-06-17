from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from database import save_mood, get_mood_counts, get_mood_question  # hinzugefügt get_mood_question
import logging

logger = logging.getLogger(__name__)

async def mood_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Startet das Stimmungsbarometer im Gruppenchat mit der aktuellen Frage aus DB."""
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return await update.message.reply_text("❌ Dieser Befehl ist nur in Gruppen nutzbar.")

    # Frage aus Datenbank laden
    question = get_mood_question(chat.id)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👍", callback_data="mood_like"),
        InlineKeyboardButton("👎", callback_data="mood_dislike"),
        InlineKeyboardButton("🤔", callback_data="mood_think"),
    ]])
    await update.message.reply_text(question, reply_markup=kb)

async def mood_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verarbeitet Klicks auf die Stimmungs-Buttons und zeigt Live-Auswertung."""
    query = update.callback_query
    await query.answer()
    mood_map = {
        "mood_like":    "👍",
        "mood_dislike": "👎",
        "mood_think":   "🤔",
    }
    mood = mood_map.get(query.data)
    chat_id = query.message.chat.id
    msg_id  = query.message.message_id
    user_id = query.from_user.id

    # Frage erneut aus DB laden
    question = get_mood_question(chat_id)

    save_mood(chat_id, msg_id, user_id, mood)
    counts = get_mood_counts(chat_id, msg_id)

    # Live-Update der Auswertung
    text = (
        f"{question}\n\n"  # Frage erneut anzeigen
        f"👍 {counts.get('👍',0)}   "
        f"👎 {counts.get('👎',0)}   "
        f"🤔 {counts.get('🤔',0)}"
    )
    await query.edit_message_text(text, reply_markup=query.message.reply_markup)

# Registrierungs-Funktion

def register_mood(app):
    app.add_handler(CommandHandler("mood", mood_command))
    app.add_handler(CallbackQueryHandler(mood_callback, pattern="^mood_"))
