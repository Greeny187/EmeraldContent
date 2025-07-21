import os
import datetime
import re
import logging
from datetime import date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, ChatMemberHandler
from telegram.error import BadRequest
from database import (register_group, get_registered_groups, get_rules, set_welcome, set_rules, set_farewell, add_member, 
remove_member, inc_message_count, assign_topic, remove_topic, has_topic, set_mood_question, get_farewell, get_welcome)
from statistic import get_group_meta, get_member_stats, get_message_insights, get_trend_analysis, get_engagement_metrics, DEVELOPER_IDS
from patchnotes import __version__, PATCH_NOTES
from utils import clean_delete_accounts_for_chat, is_deleted_account, tr
from user_manual import help_handler
from menu import show_group_menu
from access import get_visible_groups

logger = logging.getLogger(__name__)

async def error_handler(update, context):
    """Fängt alle nicht abgefangenen Errors auf, loggt und benachrichtigt Telegram-Dev-Chat."""
    logger.error("Uncaught exception", exc_info=context.error)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type in ("group", "supergroup"):
        register_group(chat.id, chat.title)
        return await update.message.reply_text("✅ Gruppe registriert! Geh privat auf /menu.")

    if chat.type == "private":
        all_groups = get_registered_groups()
        visible_groups = await get_visible_groups(user.id, context.bot, all_groups)

        if not visible_groups:
            return await update.message.reply_text(
                "🚫 Du bist in keiner Gruppe Admin, in der der Bot aktiv ist.\n"
                "➕ Füge den Bot in eine Gruppe ein und gib ihm Adminrechte."
            )

        keyboard = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid, title in visible_groups]
        markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🔧 Wähle eine Gruppe:", reply_markup=markup)

async def menu_command(update, context):
    user = update.effective_user
    chat_id = context.user_data.get("selected_chat_id")
    if not chat_id:
        # noch keine Gruppe ausgewählt → in die Auswahl springen
        await update.message.reply_text("🚧 Keine Gruppe ausgewählt. Bitte zuerst /menu in einer Gruppe nutzen.")
        return
    await show_group_menu(update, chat_id)

    if not chat_id:
        # Kein Chat ausgewählt → nutzerfreundlich zurück auf Start-Logik
        all_groups = get_registered_groups()
        visible_groups = await get_visible_groups(user.id, context.bot, all_groups)

        if not visible_groups:
            return await update.message.reply_text(
                "🚫 Du bist in keiner Gruppe Admin, in der der Bot aktiv ist.\n"
                "➕ Füge den Bot in eine Gruppe ein und gib ihm Adminrechte."
            )

        keyboard = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid, title in visible_groups]
        markup = InlineKeyboardMarkup(keyboard)
        return await update.message.reply_text("🔧 Wähle zuerst eine Gruppe:", reply_markup=markup)
    await show_group_menu(update, chat_id)

async def version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Version {__version__}\n\nPatchnotes:\n{PATCH_NOTES}")

async def message_logger(update, context):
    logger.info(f"💬 message_logger aufgerufen in Chat {update.effective_chat.id}")
    msg = update.effective_message
    if msg.chat.type in ("group", "supergroup") and msg.from_user:
        inc_message_count(msg.chat.id, msg.from_user.id, date.today())
        # neu: stelle sicher, dass jeder Schreiber in die members-Tabelle kommt
        try:
            add_member(msg.chat.id, msg.from_user.id)
            logger.info(f"➕ add_member via message_logger: chat={msg.chat.id}, user={msg.from_user.id}")
        except Exception as e:
            logger.info(f"Fehler add_member in message_logger: {e}", exc_info=True)

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
            # 0) Text in eine Variable auslagern
                warning_text = (f"⚠️ @{user.username or user.first_name}, "
                    "Linkposting ist nur für Administratoren, Inhaber und Themenbesitzer erlaubt."
                )
                try:
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text=warning_text,
                        parse_mode=None
                    )
                    await message.delete()
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

async def set_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    topic_id = msg.message_thread_id
    topic_name = None

    if topic_id:
        try:
            topic_info = await context.bot.get_forum_topic(chat.id, topic_id)
            topic_name = topic_info.name
        except Exception as e:
            logger.warning(f"⚠️ Konnte Topicname nicht laden: {e}")
            
    # DEBUG: eingehende Parameter loggen
    logger.debug(
        "🔍 set_topic called by %s in chat %s: args=%s, entities=%s, has_reply=%s",
        msg.from_user.id,
        chat.id,
        context.args,
        [ent.type for ent in (msg.entities or [])],
        bool(msg.reply_to_message)
    )

    target = None
    # 1) Reply-Fallback, nur wenn es kein Bot ist
    if msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        original_author = getattr(msg.reply_to_message, 'forward_from', None)
        target = original_author or msg.reply_to_message.from_user

    # 2) Text-Mention (aus Menü) – liefert ent.user direkt
    if not target and msg.entities:
        for ent in msg.entities:
            if ent.type == MessageEntity.TEXT_MENTION and getattr(ent, 'user', None):
                target = ent.user
                break

    # 3) Plain @username (nur Admins)
    if not target and msg.entities:
        for ent in msg.entities:
            if ent.type == MessageEntity.MENTION:
                username = msg.text[ent.offset:ent.offset + ent.length].lstrip('@').lower()
                admins = await context.bot.get_chat_administrators(chat.id)
                for adm in admins:
                    if adm.user.username and adm.user.username.lower() == username:
                        target = adm.user
                        break
                break

    # 4) Fallback: im Thread ohne Reply → Ausführenden User nehmen
    if not target and topic_id:
        target = msg.from_user

    # 5) Wenn immer noch kein Ziel – Fehlermeldung
    if not target:
        return await msg.reply_text("⚠️ Ich konnte keinen gültigen User finden. Bitte antworte auf seine Nachricht oder nutze eine Mention.")

    # 6) In DB speichern und Bestätigung
    assign_topic(chat.id, target.id, topic_id or 0, topic_name)
    name = f"@{target.username}" if target.username else target.first_name
    await msg.reply_text(f"✅ {name} wurde als Themenbesitzer zugewiesen.")
    
async def remove_topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg    = update.effective_message
    chat   = update.effective_chat
    sender = update.effective_user

    # 0) Nur Admins dürfen
    admins = await context.bot.get_chat_administrators(chat.id)
    if sender.id not in [admin.user.id for admin in admins]:
        return await msg.reply_text("❌ Nur Admins dürfen Themen entfernen.")
    
    # 1) Reply-Fallback (wenn per Reply getippt wird):
    target = None
    if msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        target = msg.reply_to_message.from_user

    # 2) Text-Mention aus Menü (ent.user ist direkt verfügbar):
    if not target and msg.entities:
        for ent in msg.entities:
            if ent.type == MessageEntity.TEXT_MENTION and getattr(ent, 'user', None):
                target = ent.user
                break
            # Inline-Link-Mention: tg://user?id=…
            if ent.type == MessageEntity.TEXT_LINK and ent.url.startswith("tg://user?id="):
                uid = int(ent.url.split("tg://user?id=")[1])
                target = await context.bot.get_chat_member(chat.id, uid)
                target = target.user
                break

    # 3) @username-Mention (für alle, nicht nur Admins):
    if not target and context.args:
        text = context.args[0]
        name = text.lstrip('@')
        # suche in Chat-Admins und -Mitgliedern
        try:
            member = await context.bot.get_chat_member(chat.id, name)
            target = member.user
        except BadRequest:
            target = None

    # 4) Wenn immer noch kein Ziel → Usage-Hinweis
    if not target:
        return await msg.reply_text(
            "⚠️ Ich konnte keinen User finden. Bitte antworte auf seine Nachricht "
            "oder nutze eine Mention (z.B. aus dem Menü)."
        )

    # 5) In DB löschen und Bestätigung
    remove_topic(chat.id, target.id)
    display = f"@{target.username}" if target.username else target.first_name
    await msg.reply_text(f"🚫 {display} wurde als Themenbesitzer entfernt.")


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

    # 0) Service-Messages behandeln: new_chat_members / left_chat_member
    msg = update.message
    if msg:
        chat_id = msg.chat.id
        # a) Neue Mitglieder
        if msg.new_chat_members:
            for user in msg.new_chat_members:
                # Willkommen wie unten
                rec = get_welcome(chat_id)
                if rec:
                    photo_id, text = rec
                    text = (text or "").replace(
                        "{user}", f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
                    )
                    if photo_id:
                        await context.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML")
                    else:
                        await context.bot.send_message(chat_id, text=text, parse_mode="HTML")
                add_member(chat_id, user.id)
            return
        # b) Verlassene Mitglieder
        if msg.left_chat_member:
            user = msg.left_chat_member
            rec = get_farewell(chat_id)
            if rec:
                photo_id, text = rec
                text = (text or "").replace(
                    "{user}", 
                    f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
                )
                if photo_id:
                    await context.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML")
                else:
                    await context.bot.send_message(chat_id, text=text, parse_mode="HTML")
            remove_member(chat_id, user.id)
            return

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
    
    cm = update.chat_member or update.my_chat_member
    if not cm:
        return
    chat_id = cm.chat.id
    user = cm.new_chat_member.user
    status = cm.new_chat_member.status
    logger.info(f"🔔 track_members aufgerufen: chat_id={update.effective_chat and update.effective_chat.id}, user={cm.new_chat_member.user.id}, status={cm.new_chat_member.status}")

async def cleandelete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    count   = await clean_delete_accounts_for_chat(chat_id, context.bot)
    await update.message.reply_text(
        f"✅ Gelöschte Accounts entfernt: {count}"
    )

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

async def stats_dev_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in DEVELOPER_IDS:
        return await update.effective_message.reply_text("❌ Zugriff verweigert.")

    chat_id = context.user_data.get("stats_group_id") or update.effective_chat.id
    # Zeitfenster: letzte 7 Tage
    end   = datetime.utcnow()
    start = end - timedelta(days=7)

    meta       = get_group_meta(chat_id)
    members    = get_member_stats(chat_id, start)
    insights   = get_message_insights(chat_id, start, end)
    engage     = get_engagement_metrics(chat_id, start, end)
    trends     = get_trend_analysis(chat_id, periods=4)

    text = (
        f"*Dev-Dashboard für Gruppe {chat_id} (letzte 7 Tage)*\n"
        f"• Beschreibung: {meta['description']}\n"
        f"• Topics: {meta['topics']}  • Bots: {meta['bots']}\n"
        f"• Neue Member: {members['new']}  🔴 Left: {members['left']}  💤 Inaktiv: {members['inactive']}\n\n"

        f"*Nachrichten*  Gesamt: {insights['total']}\n"
        f"  • Fotos: {insights['photo']}  Videos: {insights['video']}  Sticker: {insights['sticker']}\n"
        f"  • Voice: {insights['voice']}  Location: {insights['location']}  Polls: {insights['polls']}\n\n"

        f"*Engagement*  Antwort-Rate: {engage['reply_rate_pct']} %  Ø-Delay: {engage['avg_delay_s']} s\n\n"

        f"*Trend (Woche → Menge)*\n"
    )
    for week, cnt in trends.items():
        text += f"  – {week}: {cnt}\n"

    await update.effective_message.reply_text(text, parse_mode="Markdown")


def register_handlers(app):

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("version", version))
    app.add_handler(CommandHandler("rules", show_rules_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("settopic", set_topic))
    
    app.add_handler(CommandHandler("removetopic", remove_topic_cmd))
    app.add_handler(CommandHandler("cleandeleteaccounts", clean_delete_accounts_for_chat, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("dashboard", stats_dev_command))
    app.add_handler(CommandHandler("sync_admins_all", sync_admins_all, filters=filters.ChatType.PRIVATE))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_logger), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mood_question_reply), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler),       group=2)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO) & ~filters.COMMAND, edit_content))

    app.add_handler(help_handler)

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, track_members), group=1)
    app.add_handler(ChatMemberHandler(track_members, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(track_members, ChatMemberHandler.MY_CHAT_MEMBER))