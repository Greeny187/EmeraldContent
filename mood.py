from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler, filters
from database import save_mood, get_mood_counts, get_mood_question, set_mood_topic, get_mood_topic
import logging

logger = logging.getLogger(__name__)

async def mood_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Startet das Stimmungsbarometer im Gruppenchat mit der aktuellen Frage aus DB."""
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return await context.bot.send_message(chat_id=chat.id, text="❌ Dieser Befehl ist nur in Gruppen nutzbar.")

    # Frage und Topic laden
    question = get_mood_question(chat.id) or "Wie ist deine Stimmung?"
    try:
        chat_info = await context.bot.get_chat(chat.id)
        is_forum = bool(getattr(chat_info, "is_forum", False))
    except Exception:
        is_forum = False

    topic_id = get_mood_topic(chat.id)  # 0 oder None => kein gesetztes Topic

    # In Foren zwingend Topic erforderlich
    if is_forum and not topic_id:
        return await context.bot.send_message(
            chat_id=chat.id,
            text="⚠️ Dieses Chat ist ein Forum. Bitte setze zuerst ein Mood-Topic via /setmoodtopic in dem gewünschten Thema."
        )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👍", callback_data="mood_like"),
        InlineKeyboardButton("👎", callback_data="mood_dislike"),
        InlineKeyboardButton("🤔", callback_data="mood_think"),
    ]])

    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=question,
            reply_markup=kb,
            message_thread_id=topic_id or None  # in Nicht-Foren None erlaubt
        )
    except Exception:
        logger.exception("Fehler beim Senden der Mood-Nachricht")
        await context.bot.send_message(chat_id=chat.id, text="⚠️ Mood konnte nicht gesendet werden. Prüfe das gesetzte Topic.")

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

async def set_mood_topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    user = update.effective_user

    if chat.type not in ("group", "supergroup"):
        return await msg.reply_text("❌ Dieser Befehl ist nur in Gruppen nutzbar.")

    # Admin-Check
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        if user.id not in {admin.user.id for admin in admins}:
            return await msg.reply_text("❌ Nur Administratoren können das Mood-Topic festlegen.")
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return await msg.reply_text("⚠️ Fehler bei der Überprüfung der Administratorrechte.")

    # Foren-Check
    try:
        chat_info = await context.bot.get_chat(chat.id)
        is_forum = bool(getattr(chat_info, "is_forum", False))
    except Exception:
        is_forum = False

    # 1) Topic aus aktuellem Thread (wenn der Befehl im Thema ausgeführt wurde)
    topic_id = msg.message_thread_id
    # 2) Fallback: auf eine Nachricht aus dem Thema antworten
    if topic_id is None and msg.reply_to_message:
        topic_id = msg.reply_to_message.message_thread_id

    # In Foren ist ein Topic verpflichtend
    if is_forum and topic_id is None:
        return await msg.reply_text(
            "⚠️ Bitte führe /setmoodtopic direkt in dem gewünschten Forum-Thema aus "
            "oder antworte auf eine Nachricht darin."
        )

    try:
        # Speichern (None erlaubt für Nicht-Foren; in Foren ist topic_id garantiert gesetzt)
        set_mood_topic(chat.id, int(topic_id) if topic_id is not None else None)
        await msg.reply_text(
            f"✅ Mood-Topic gesetzt auf ID {topic_id}." if topic_id is not None
            else "✅ Mood-Topic zurückgesetzt (kein Thread erforderlich)."
        )
    except Exception as e:
        logger.error(f"Error setting mood topic: {e}")
        await msg.reply_text("⚠️ Fehler beim Speichern des Mood-Topics in der Datenbank.")

# Registrierungs-Funktion

def register_mood(app):
    app.add_handler(CommandHandler("mood", mood_command))
    app.add_handler(CallbackQueryHandler(mood_callback, pattern="^mood_"))
    app.add_handler(CommandHandler("setmoodtopic", set_mood_topic_cmd, filters=filters.ChatType.GROUPS))
