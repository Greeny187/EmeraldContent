from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from database import save_mood, get_mood_counts, get_mood_question
import logging

logger = logging.getLogger(__name__)

async def mood_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Startet das Stimmungsbarometer im Gruppenchat mit der aktuellen Frage aus DB."""
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return await context.bot.send_message(chat_id=chat.id, text="❌ Dieser Befehl ist nur in Gruppen nutzbar.")

    # Frage aus Datenbank laden
    question = get_mood_question(chat.id)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👍", callback_data="mood_like"),
        InlineKeyboardButton("👎", callback_data="mood_dislike"),
        InlineKeyboardButton("🤔", callback_data="mood_think"),
    ]])
    await context.bot.send_message(chat_id=chat.id, text=question, reply_markup=kb)

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

    logger.info(f"mood_callback: chat_id={chat_id}, msg_id={msg_id}, user_id={user_id}, mood={mood}")
    try:
        save_mood(chat_id, msg_id, user_id, mood)
        logger.debug("save_mood erfolgreich")
        counts = get_mood_counts(chat_id, msg_id)
        logger.debug(f"get_mood_counts → {counts}")
    except Exception:
        logger.exception("Fehler beim Speichern oder Zählen der Stimmen")
        # gib dem User ein Feedback und beende
        return await query.answer("⚠️ Da ist etwas schiefgelaufen", show_alert=True)

    # Kurzes Feedback (Notification wegklicken)
    await query.answer(text="Deine Stimme wurde erfasst", show_alert=False)

    # Neue Buttons mit aktuellen Counts bauen
    buttons = [
        InlineKeyboardButton(f"👍 {counts.get('👍',0)}", callback_data="mood_like"),
        InlineKeyboardButton(f"👎 {counts.get('👎',0)}", callback_data="mood_dislike"),
        InlineKeyboardButton(f"🤔 {counts.get('🤔',0)}", callback_data="mood_think"),
    ]
    logger.info(f"neue Button-Labels: {[b.text for b in buttons]}")
    new_kb = InlineKeyboardMarkup([buttons])

    # Nachricht updaten und Fehler loggen
    try:
        await query.edit_message_text(text=question, reply_markup=new_kb)
        logger.debug("edit_message_text erfolgreich ausgeführt")
    except Exception:
        logger.exception("Fehler beim Editieren der Mood-Nachricht")

# Registrierungs-Funktion

def register_mood(app):
    app.add_handler(CommandHandler("mood", mood_command))
    app.add_handler(CallbackQueryHandler(mood_callback, pattern="^mood_"))
