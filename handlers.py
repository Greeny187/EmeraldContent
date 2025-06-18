import os
import datetime
import re
import logging
from datetime import date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, ChatMemberHandler
from database import (register_group, get_registered_groups, get_rules, set_welcome, set_rules, set_farewell, add_member, 
remove_member, list_members, inc_message_count, assign_topic, remove_topic, has_topic, set_mood_question, set_rss_topic, 
get_rss_feeds, count_members, get_farewell, get_welcome)
from patchnotes import __version__, PATCH_NOTES
from utils import clean_delete_accounts_for_chat, is_deleted_account
from user_manual import help_handler

logger = logging.getLogger(__name__)

async def error_handler(update, context):
    """Fängt alle nicht abgefangenen Errors auf, loggt und benachrichtigt Telegram-Dev-Chat."""
    logger.error("Uncaught exception", exc_info=context.error)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        register_group(chat.id, chat.title)
        return await update.message.reply_text(
            "✅ Gruppe registriert! Geh privat auf /menu."
        )
    if chat.type == "private":
        groups = get_registered_groups()
        if not groups:
            return await update.message.reply_text(
                "Keine Gruppen registriert. Führe `/start` in Gruppe aus."
            )
        keyboard = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid,title in groups]
        markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🔧 Wähle eine Gruppe:", reply_markup=markup)

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        return await update.message.reply_text("Bitte im privaten Chat nutzen.")
    chat_id = context.user_data.get("selected_chat_id")
    if not chat_id:
        return await update.message.reply_text(
            "🚫 Keine Gruppe gewählt. `/start` → Gruppe auswählen."
        )
    from menu import show_group_menu  # import hier, da menu hängt utils, nicht handlers
    await show_group_menu(update, chat_id)

async def version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Version {__version__}\n\nPatchnotes:\n{PATCH_NOTES}")

async def message_logger(update, context):
    msg = update.effective_message
    if msg.chat.type in ("group", "supergroup") and msg.from_user:
        inc_message_count(msg.chat.id, msg.from_user.id, date.today())
        # neu: stelle sicher, dass jeder Schreiber in die members-Tabelle kommt
        try:
            add_member(msg.chat.id, msg.from_user.id)
            logger.debug(f"➕ add_member via message_logger: chat={msg.chat.id}, user={msg.from_user.id}")
        except Exception as e:
            logger.error(f"Fehler add_member in message_logger: {e}", exc_info=True)
    return await text_handler(update, context)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    message = update.message

    # Spam-/Linkschutz
    if chat.type in ('group', 'supergroup') and message.text:
        # Link erkannt?
        if re.search(r'https?://|\bwww\.', message.text.lower()):
            # Admins inkl. Creator abfragen
            admins = await context.bot.get_chat_administrators(chat.id)
            is_admin       = any(m.user.id == user.id for m in admins if m.status in ("administrator","creator"))
            # Anonyme Inhaber/Admins: sender_chat == Gruppen-Chat selbst
            is_anon_admin  = hasattr(message, "sender_chat") and getattr(message, "sender_chat", None) and message.sender_chat.id == chat.id
            # Themenbesitzer
            is_topic_owner = has_topic(chat.id, user.id)

            # Nur löschen, wenn keiner der Ausnahmen greift
            if not (is_admin or is_anon_admin or is_topic_owner):
                try:
                    await message.delete()
                    await context.bot.send_message(
                        chat_id=chat.id,
                        reply_to_message_id=message.message_id,
                        text=(
                            f"⚠️ @{user.username or user.first_name}, "
                            "Linkposting ist nur für Administratoren, Inhaber und Themenbesitzer erlaubt."
                        )
                    )
                except Exception as e:
                    logger.error(f"Löschen fehlgeschlagen: {e}")
                return

async def edit_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Nur aktiv, wenn zuvor im Menü „Bearbeiten“ gedrückt wurde
    if "last_edit" not in context.user_data:
        return

    chat_id, action = context.user_data.pop("last_edit")
    msg = update.message

    # Foto + Caption oder reiner Text
    if msg.photo:
        photo_id = msg.photo[-1].file_id
        text = msg.caption or ""
    else:
        photo_id = None
        text = msg.text or ""

    # In DB schreiben
    if action == "welcome_edit":
        set_welcome(chat_id, photo_id, text)
        label = "Begrüßung"
    elif action == "rules_edit":
        set_rules(chat_id, photo_id, text)
        label = "Regeln"
    elif action == "farewell_edit":
        set_farewell(chat_id, photo_id, text)
        label = "Farewell-Nachricht"
    else:
        return

    # Bestätigung mit Zurück-Button ins Menü
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅ Zurück", callback_data=f"{chat_id}_{action.split('_')[0]}")
    ]])
    await msg.reply_text(f"✅ {label} gesetzt.", reply_markup=kb)

async def mood_question_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Prüfen, ob wir auf eine Mood-Frage warten
    if not context.user_data.get("awaiting_mood_question"):
        return  # nichts tun, zurück an den normalen Handler

    new_q = update.effective_message.text
    chat_id = context.user_data.pop("mood_group_id")
    # Frage speichern
    set_mood_question(chat_id, new_q)
    # Flag löschen
    context.user_data.pop("awaiting_mood_question", None)

    await update.effective_message.reply_text(
        f"✅ Mood-Frage gesetzt auf:\n» {new_q}«"
    )

async def set_topic(update, context):
    chat = update.effective_chat
    msg = update.effective_message

    # Nur Username-Argument zulassen:
    if not context.args or not context.args[0].startswith('@'):
        return await msg.reply_text("⚠️ Bitte gib einen Benutzernamen an, z.B. `/settopic @username`.", parse_mode="Markdown")
    username = context.args[0][1:]
    try:
        member = await context.bot.get_chat_member(chat.id, username[1:])
        target = member.user
    except Exception: 
        return await msg.reply_text(f"⚠️ Benutzer `@{username}` nicht gefunden.", parse_mode="Markdown")

    # 5) In DB speichern und Bestätigung
    assign_topic(chat.id, target.id)
    # Anzeige-Name
    name = f"@{target.username}" if target.username else target.first_name
    await msg.reply_text(f"✅ {name} wurde als Themenbesitzer zugewiesen.")
    
async def remove_topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].startswith('@'):
        await update.message.reply_text("⚠️ Beispiel: /removetopic @alex")
        return
    username = context.args[0][1:]
    chat = update.effective_chat
    sender = update.effective_user
    admins = await context.bot.get_chat_administrators(chat.id)
    if sender.id not in [admin.user.id for admin in admins]:
        await update.message.reply_text("Nur Admins dürfen Themen entfernen.")
        return
    try:
        member = await context.bot.get_chat_member(chat.id, username)
        remove_topic(chat.id, member.user.id)
        await update.message.reply_text(f"🚫 @{username} wurde das Thema entzogen.")
    except Exception as e:
        logger.error(f"/removetopic error: {e}")
        await update.message.reply_text("⚠️ Fehler beim Entfernen des Themas.")


async def show_rules_cmd(update, context):
    chat_id = update.effective_chat.id
    rec = get_rules(chat_id)
    if not rec:
        await update.message.reply_text("Keine Regeln gesetzt.")
    else:
        photo_id, text = rec
        if photo_id:
            await context.bot.send_photo(chat_id, photo_id, caption=text or "")
        else:
            await update.message.reply_text(text)

async def track_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cm = update.chat_member
    logger.info(f"🔔 track_members aufgerufen: chat_id={update.effective_chat and update.effective_chat.id}, user={cm.new_chat_member.user.id}, status={cm.new_chat_member.status}")
    user = cm.new_chat_member.user
    status = cm.new_chat_member.status
    cm = update.chat_member or update.my_chat_member
    chat_id = cm.chat.id

    # 1) Willkommen verschicken
    if status in ("member", "administrator", "creator"):
        rec = get_welcome(chat_id)
        if rec:
            photo_id, text = rec
            # Nutzer direkt ansprechen:
            text = (text or "").replace("{user}", f"<a href='tg://user?id={user.id}'>{user.first_name}</a>")
            if photo_id:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_id,
                    caption=text,
                    parse_mode="HTML"
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML"
                )
  
        try:
            add_member(chat_id, user.id)
            logger.info(f"✅ add_member in DB: chat={chat_id}, user={user.id}")
        except Exception as e:
            logger.error(f"❌ add_member fehlgeschlagen: {e}", exc_info=True)
        return

    # 2) Abschied verschicken
    if status in ("left", "kicked"):
        rec = get_farewell(chat_id)
        if rec:
            photo_id, text = rec
            text = (text or "").replace("{user}", f"<a href='tg://user?id={user.id}'>{user.first_name}</a>")
            if photo_id:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_id,
                    caption=text,
                    parse_mode="HTML"
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML"
                )
        remove_member(chat_id, user.id)
        return

async def clean_delete_accounts_for_chat(chat_id: int, bot) -> int:
    """
    Entfernt alle gelöschten Accounts in der DB-Liste per Ban+Unban
    und gibt die Anzahl der entfernten User zurück.
    """
    removed = []
    for user_id in list_members(chat_id):
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if is_deleted_account(member):
                await bot.ban_chat_member(chat_id, user_id)
                await bot.unban_chat_member(chat_id, user_id)
                remove_member(chat_id, user_id)
                removed.append(user_id)
        except Exception as e:
            logger.error(f"Error cleaning user {user_id} in chat {chat_id}: {e}")
    return len(removed)

async def set_rss_topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg  = update.effective_message

    # Nur in Gruppen/Supergruppen zulassen
    if chat.type not in ("group", "supergroup"):
        return await msg.reply_text("❌ `/settopicrss` nur in Gruppen möglich.")

    # 1) Wenn im Thema ausgeführt, nimmt message_thread_id
    topic_id = msg.message_thread_id or None
    # 2) Oder, falls als Reply in einem Thema
    if not topic_id and msg.reply_to_message:
        topic_id = msg.reply_to_message.message_thread_id

    if not topic_id:
        return await msg.reply_text(
            "⚠️ Bitte führe `/settopicrss` in dem gewünschten Forum-Thema aus "
            "oder antworte auf eine Nachricht darin."
        )

    # In DB speichern
    set_rss_topic(chat.id, topic_id)
    await msg.reply_text(f"✅ RSS-Posting-Thema gesetzt auf Topic {topic_id}.")

async def sync_admins_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dev = os.getenv("DEVELOPER_CHAT_ID")
    if str(update.effective_user.id) != dev:
        return await update.message.reply_text("❌ Nur Entwickler darf das tun.")
    total = 0
    for chat_id, _ in get_registered_groups():
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            for adm in admins:
                add_member(chat_id, adm.user.id)
                total += 1
        except Exception as e:
            logger.error(f"Fehler bei Sync Admins für {chat_id}: {e}")
    await update.message.reply_text(f"✅ {total} Admin-Einträge in der DB angelegt.")

async def dashboard_command(update, context):
    user_id = update.effective_user.id
    dev_id = os.getenv("DEVELOPER_CHAT_ID")
    if str(user_id) != str(dev_id):
        return await update.message.reply_text("❌ Zugriff verweigert.")

    # Metriken sammeln
    total_groups = len(get_registered_groups())
    total_rss = len(get_rss_feeds())
    total_users  = sum(count_members(chat_id) for chat_id, _ in get_registered_groups())
    uptime = datetime.datetime.now() - context.bot_data.get('start_time', datetime.datetime.now())

    msg = (
        f"🤖 *Bot Dashboard*\n"
        f"\n• Startzeit: `{context.bot_data.get('start_time')}`"
        f"\n• Uptime: `{str(uptime).split('.')[0]}`"
        f"\n• Gruppen: `{total_groups}`"
        f"\n• RSS-Feeds: `{total_rss}`"
        f"\n• Gesamt-Mitglieder: `{total_users}`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


def register_handlers(app):

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("version", version))
    app.add_handler(CommandHandler("rules", show_rules_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("settopic", set_topic))
    app.add_handler(CommandHandler("settopicrss", set_rss_topic_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("removetopic", remove_topic_cmd))
    app.add_handler(CommandHandler("cleandeleteaccounts", clean_delete_accounts_for_chat, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("dashboard", dashboard_command))
    app.add_handler(CommandHandler("sync_admins_all", sync_admins_all, filters=filters.ChatType.PRIVATE))

    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY, mood_question_reply))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_logger))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO) & ~filters.COMMAND, edit_content))

    app.add_handler(help_handler)

    app.add_handler(ChatMemberHandler(track_members, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(track_members, ChatMemberHandler.MY_CHAT_MEMBER))