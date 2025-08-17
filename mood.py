import logging
import inspect
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler, filters
from database import save_mood, get_mood_counts, get_mood_question, set_mood_topic, get_mood_topic

logger = logging.getLogger(__name__)

async def _call_db(fn, *args, **kwargs):
    """Hilfsfunktion: unterstützt sync und async DB-Funktionen."""
    try:
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)
    except Exception:
        logger.exception("DB-Aufruf fehlgeschlagen: %s", getattr(fn, "__name__", fn))
        raise

async def mood_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Startet das Stimmungsbarometer."""
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return await context.bot.send_message(chat_id=chat.id, text="❌ Dieser Befehl ist nur in Gruppen nutzbar.")

    try:
        chat_info = await context.bot.get_chat(chat.id)
        is_forum = bool(getattr(chat_info, "is_forum", False))
    except Exception:
        is_forum = False

    question = (await _call_db(get_mood_question, chat.id)) or "Wie ist deine Stimmung?"
    topic_id = await _call_db(get_mood_topic, chat.id)

    if is_forum and not topic_id:
        return await context.bot.send_message(
            chat_id=chat.id,
            text="⚠️ Dieses Chat ist ein Forum. Bitte setze zuerst ein Mood-Topic via /setmoodtopic im gewünschten Thema."
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
            message_thread_id=topic_id or None
        )
    except Exception:
        logger.exception("Fehler beim Senden der Mood-Nachricht")
        await context.bot.send_message(chat_id=chat.id, text="⚠️ Mood konnte nicht gesendet werden. Prüfe das gesetzte Topic.")

async def set_mood_topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Setzt das Mood-Topic (in Foren Pflicht)."""
    chat = update.effective_chat
    msg = update.effective_message
    user = update.effective_user

    logger.info(f"setmoodtopic aufgerufen von User {user.id} in Chat {chat.id}")

    if chat.type not in ("group", "supergroup"):
        return await msg.reply_text("❌ Dieser Befehl ist nur in Gruppen nutzbar.")

    # Admin-Check
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        admin_ids = {a.user.id for a in admins}
        logger.info(f"Admin-IDs: {admin_ids}, User-ID: {user.id}")
        if user.id not in admin_ids:
            return await msg.reply_text("❌ Nur Administratoren können das Mood-Topic festlegen.")
    except Exception as e:
        logger.error("Admin-Check fehlgeschlagen: %s", e)
        return await msg.reply_text("⚠️ Fehler bei der Überprüfung der Administratorrechte.")

    # Topic-ID ermitteln
    topic_id = msg.message_thread_id
    if topic_id is None and msg.reply_to_message:
        topic_id = msg.reply_to_message.message_thread_id
        
    logger.info(f"Ermittelte Topic-ID: {topic_id}")

    # Foren-Check
    try:
        chat_info = await context.bot.get_chat(chat.id)
        is_forum = bool(getattr(chat_info, "is_forum", False))
        logger.info(f"Chat ist Forum: {is_forum}")
    except Exception:
        is_forum = False

    if is_forum and topic_id is None:
        return await msg.reply_text(
            "⚠️ Bitte führe /setmoodtopic direkt in dem gewünschten Forum-Thema aus "
            "oder antworte auf eine Nachricht darin."
        )

    try:
        logger.info(f"Speichere Mood-Topic: Chat {chat.id}, Topic {topic_id}")
        await _call_db(set_mood_topic, chat.id, int(topic_id) if topic_id is not None else None)
        await msg.reply_text(
            f"✅ Mood-Topic gesetzt auf ID {topic_id}." if topic_id is not None
            else "✅ Mood-Topic zurückgesetzt (kein Thread erforderlich)."
        )
        logger.info(f"Mood-Topic erfolgreich gespeichert für Chat {chat.id}")
    except Exception as e:
        logger.error(f"Fehler beim Speichern des Mood-Topics: {e}")
        return await msg.reply_text("⚠️ Fehler beim Speichern des Mood-Topics in der Datenbank.")

async def mood_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reagiert auf Klicks der Mood-Buttons."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    chat = update.effective_chat
    message_id = query.message.message_id  # <- Korrekte message_id verwenden
    
    data = query.data  # z.B. mood_like
    mood = data.split("_", 1)[1] if "_" in data else "like"
    
    try:
        # Korrekte Parameter-Reihenfolge: chat_id, message_id, user_id, mood
        await _call_db(save_mood, chat.id, message_id, user.id, mood)
        counts = await _call_db(get_mood_counts, chat.id, message_id)
        
        # Text mit aktuellen Zählungen aktualisieren
        original_text = query.message.text
        txt = f"{original_text}\n\n👍 {counts.get('like',0)} | 👎 {counts.get('dislike',0)} | 🤔 {counts.get('think',0)}"
        
        # Message mit neuen Zählungen editieren
        await query.edit_message_text(
            text=txt,
            reply_markup=query.message.reply_markup
        )
        
    except Exception as e:
        logger.exception("Fehler beim Speichern der Mood-Stimme")
        await query.answer("⚠️ Konnte Stimme nicht speichern.", show_alert=True)

def register_mood(app):
    app.add_handler(CommandHandler("mood", mood_command, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("setmoodtopic", set_mood_topic_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(CallbackQueryHandler(mood_callback, pattern="^mood_"))
