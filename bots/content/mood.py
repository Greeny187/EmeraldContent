import logging
import inspect
import asyncio
import re
from telegram.constants import ChatType
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler, filters
from shared.database import save_mood, get_mood_counts, get_mood_question, set_mood_topic, get_mood_topic

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
    """Setzt das Mood-Topic für diesen Chat.
    Nutzung:
      - In Foren: Command IM gewünschten Thema ausführen (oder auf eine Nachricht in diesem Thema antworten).
      - Optional: /setmoodtopic <topic_id>  (setzt explizit auf diese Thread-ID)
      - In normalen Gruppen: setzt 'kein Topic' (globale Nutzung).
    """
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    # Nur in Gruppen/Supergruppen
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await msg.reply_text("Dieser Befehl funktioniert nur in Gruppen.")

    # Admin-Check (nur Admins dürfen setzen)
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        admin_ids = {a.user.id for a in admins}
        if user.id not in admin_ids:
            return await msg.reply_text("Nur Admins können das Mood-Topic setzen.")
    except Exception:
        # Fallback: wenn die Abfrage scheitert, lieber abbrechen als falsch setzen
        return await msg.reply_text("Konnte Adminrechte nicht prüfen. Bitte erneut versuchen.")

    # Topic-ID ermitteln
    topic_id = None

    # 1) explizites Argument erlaubt: /setmoodtopic 12345
    if context.args:
        m = re.match(r"^\d+$", context.args[0].strip())
        if m:
            topic_id = int(context.args[0].strip())

    # 2) Falls Forum: aus aktuellem Thread oder Reply übernehmen
    if topic_id is None and getattr(chat, "is_forum", False):
        topic_id = msg.message_thread_id or (
            msg.reply_to_message.message_thread_id if msg.reply_to_message else None
        )
        if topic_id is None:
            return await msg.reply_text(
                "Dies ist eine Foren-Gruppe. Bitte führe /setmoodtopic **im gewünschten Thema** aus "
                "oder antworte mit dem Befehl auf eine Nachricht in diesem Thema.\n\n"
                "Alternativ: /setmoodtopic <topic_id>",
            )

    # 3) In Nicht-Foren-Gruppen ist 'kein Topic' zulässig (globale Nutzung)
    # -> topic_id bleibt None

    # Speichern
    try:
        await _call_db(set_mood_topic, chat.id, int(topic_id) if topic_id is not None else None)
    except Exception:
        logger.exception("Fehler beim Speichern des Mood-Topic")
        return await msg.reply_text("⚠️ Konnte Mood-Topic nicht speichern.")

    # Feedback
    if topic_id is None:
        return await msg.reply_text("✅ Mood-Topic entfernt (globale Nutzung in dieser Gruppe).")
    return await msg.reply_text(f"✅ Mood-Topic gesetzt auf Thread-ID {topic_id}.")

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

# In mood.py temporär hinzufügen:
async def debug_setmoodtopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"DEBUG: setmoodtopic wurde aufgerufen in Chat {update.effective_chat.id}")
    
def register_mood(app):
    # Commands in Gruppe 0 (höhere Priorität)
    app.add_handler(CommandHandler("mood", mood_command, filters=filters.ChatType.GROUPS), group=0)
    app.add_handler(CommandHandler("setmoodtopic", set_mood_topic_cmd, filters=filters.ChatType.GROUPS), group=0)
    app.add_handler(CallbackQueryHandler(mood_callback, pattern=r"^mood_"), group=0)