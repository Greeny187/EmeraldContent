import os
import datetime
import re
import logging
from datetime import date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, ChatMemberHandler, CallbackQueryHandler
from database import (register_group, get_registered_groups, get_rules, set_welcome, set_rules, set_farewell, add_member, 
remove_member, list_members, inc_message_count, assign_topic, remove_topic, has_topic, set_mood_question, set_rss_topic, 
get_rss_feeds, count_members, get_farewell, get_welcome, get_all_channels, add_channel, add_scheduled_post, list_scheduled_posts)
from patchnotes import __version__, PATCH_NOTES
from utils import clean_delete_accounts_for_chat, is_deleted_account
from user_manual import help_handler
from access import get_visible_groups, get_visible_channels

logger = logging.getLogger(__name__)

async def error_handler(update, context):
    """Fängt alle nicht abgefangenen Errors auf, loggt und benachrichtigt Telegram-Dev-Chat."""
    logger.error("Uncaught exception", exc_info=context.error)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"/start in Chat {update.effective_chat.id} (type={update.effective_chat.type})")
    chat = update.effective_chat
    user = update.effective_user

    # 1) Gruppe registrieren
    if chat.type in ("group", "supergroup"):
        register_group(chat.id, chat.title)
        return await update.message.reply_text("✅ Gruppe registriert! Geh privat auf /menu.")

    # 2) Kanal registrieren
    if chat.type == "channel" or chat.type == "supergroup":
        from database import add_channel
        add_channel(chat.id, chat.id, chat.username, chat.title)
        return await update.effective_message.reply_text(
            "✅ Kanal registriert! Wechsle in deinen privaten Bot-Chat und sende /start."
        )
    
    if update.effective_chat.type == "private":
        user = update.effective_user

        all_groups    = get_registered_groups()    # [(chat_id, title), …]
        visible_groups   = await get_visible_groups(user.id, context.bot, all_groups)

        all_channels = get_all_channels()  # [(parent_chat_id, channel_id, username, title), …]
        visible_channels    = await get_visible_channels(user.id, context.bot, all_channels)

        if not visible_groups and not visible_channels:
            return await update.message.reply_text(
                "🚫 Du bist in keiner Gruppe oder keinem Kanal Admin, in dem der Bot aktiv ist."
            )

        keyboard = []
        for gid, title in visible_groups:
            keyboard.append([InlineKeyboardButton(f"👥 {title}", callback_data=f"group_{gid}")])
        for cid, title in visible_channels:
            keyboard.append([InlineKeyboardButton(f"📺 {title}", callback_data=f"channel_{cid}")])

        return await update.message.reply_text(
            "🔧 Wähle eine Gruppe oder einen Kanal:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type != "private":
        return await update.message.reply_text("⚠️ Bitte nutze /menu nur im privaten Chat.")

    chat_id = context.user_data.get("selected_chat_id")

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
    # 1) Reply-Fallback: vorrangig Replied-User (forward_from oder from_user)
    if msg.reply_to_message:
        # sicher auf forward_from prüfen
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

    # 4) Wenn immer noch kein Ziel – Fehlermeldung
    if not target:
        # WARN: Entities inspect – nur echte User-Objekte auslesen
        entity_info = [
            (ent.type, ent.user.id)
            for ent in (msg.entities or [])
            if getattr(ent, 'user', None)
        ]
        logger.warning(
            "❌ set_topic: kein target – args=%s, entities=%s, reply=%s",
            context.args,
            entity_info,
            bool(msg.reply_to_message)
        )
        return await msg.reply_text(
            "⚠️ Ich konnte keinen User finden. "
            "Bitte antworte auf eine Nachricht desjenigen oder verwende eine Text-Mention aus dem Menü.",
            parse_mode="Markdown"
        )

    # 5) In DB speichern und Bestätigung
    assign_topic(chat.id, target.id, topic_id or 0, topic_name)
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
    # update.chat_member liefert None, wenn es kein Chat-Member-Update ist
    cm = update.chat_member or update.my_chat_member
    if cm is None or cm.new_chat_member is None:
        return  # nichts zu tun

    chat_id = update.effective_chat.id if update.effective_chat else None
    user = cm.new_chat_member.user
    status = cm.new_chat_member.status

    logger.info(
        f"🔔 track_members aufgerufen: chat_id={chat_id}, user={user.id}, status={status}"
    )

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

async def cleandelete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    count   = await clean_delete_accounts_for_chat(chat_id, context.bot)
    await update.message.reply_text(
        f"✅ Gelöschte Accounts entfernt: {count}"
    )

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

async def channel_broadcast_menu(update, context):
    q = update.callback_query
    await q.answer()
    chan_id = int(q.data.rsplit("_",1)[1])
    # Frage nach Inhalt
    context.user_data["broadcast_chan"] = chan_id
    return await q.edit_message_text(
        "📝 Bitte sende jetzt den Broadcast-Inhalt (Text oder Foto + Text)."
    )

async def handle_broadcast_content(update, context):
    if "broadcast_chan" not in context.user_data:
        return
    chan_id = context.user_data.pop("broadcast_chan")
    msg = update.effective_message
    # Foto + Caption oder reiner Text
    if msg.photo:
        fid = msg.photo[-1].file_id
        caption = msg.caption or ""
        await context.bot.send_photo(chan_id, fid, caption=caption, parse_mode="HTML")
    else:
        await context.bot.send_message(chan_id, msg.text, parse_mode="HTML")
    return await update.message.reply_text("✅ Broadcast gesendet.")

async def channel_stats_menu(update, context):
    q = update.callback_query
    await q.answer()
    chan_id = int(q.data.rsplit("_",1)[1])
    # basic: Abonnenten‐Anzahl
    chat = await context.bot.get_chat(chan_id)
    subs = chat.get_members_count()
    text = f"📈 Kanal‐Statistiken:\n• Abonnenten: {subs}"
    return await q.edit_message_text(text)

async def channel_pins_menu(update, context):
    q = update.callback_query
    await q.answer()
    chan_id = int(q.data.rsplit("_",1)[1])
    # Letzten Pin abrufen
    pinned = (await context.bot.get_chat(chan_id)).pinned_message
    lines = ["📌 Aktuell angeheftete Nachricht:"]
    if pinned:
        lines.append(pinned.text or "(Mediennachricht)")
        lines.append(f"(ID: {pinned.message_id})")
    else:
        lines.append("– Keine –")
    buttons = [
        [InlineKeyboardButton("🔙 Zurück", callback_data=f"channel_{chan_id}")],
    ]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))

async def channel_schedule_menu(update, context):
    q = update.callback_query
    await q.answer()
    chan_id = int(q.data.rsplit("_",1)[1])
    schedules = list_scheduled_posts(chan_id)
    lines = ["🗓️ Geplante Beiträge:"]
    for text, cron in schedules:
        lines.append(f"• {cron} → «{text[:30]}…»")
    buttons = [
        [InlineKeyboardButton("➕ Neu planen", callback_data=f"ch_sched_add_{chan_id}")],
        [InlineKeyboardButton("🔙 Zurück",        callback_data=f"channel_{chan_id}")],
    ]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))

async def channel_settings_menu(update, context):
    q = update.callback_query
    await q.answer()
    chan_id = int(q.data.rsplit("_",1)[1])
    buttons = [
        [InlineKeyboardButton("✏️ Titel ändern", callback_data=f"ch_settitle_{chan_id}")],
        [InlineKeyboardButton("📝 Beschreibung", callback_data=f"ch_setdesc_{chan_id}")],
        [InlineKeyboardButton("🔙 Zurück",         callback_data=f"channel_{chan_id}")],
    ]
    return await q.edit_message_text("⚙️ Kanal‐Einstellungen:", reply_markup=InlineKeyboardMarkup(buttons))

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

    # 1) /start in Kanälen (als Channel-Post)
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.Regex(r"^/start(@\w+)?$"), start), group=-2)
    # 2) /start in Gruppen (group & supergroup)
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.GROUPS), group=0)
    # 3) /start im privaten Chat
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE), group=1)
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("version", version))
    app.add_handler(CommandHandler("rules", show_rules_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("settopic", set_topic))
    app.add_handler(CommandHandler("settopicrss", set_rss_topic_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("removetopic", remove_topic_cmd))
    app.add_handler(CommandHandler("cleandeleteaccounts", clean_delete_accounts_for_chat, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("dashboard", dashboard_command))
    app.add_handler(CommandHandler("sync_admins_all", sync_admins_all, filters=filters.ChatType.PRIVATE))

    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.TEXT|filters.PHOTO) & ~filters.COMMAND,edit_content), group=-1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_logger), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mood_question_reply), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler),       group=2)

    app.add_handler(CallbackQueryHandler(channel_broadcast_menu, pattern=r"^ch_broadcast_\d+$"), group=5)
    app.add_handler(MessageHandler(filters.PRIVATE & ~filters.COMMAND, handle_broadcast_content), group=6)

    app.add_handler(help_handler)

    app.add_handler(ChatMemberHandler(track_members, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(track_members, ChatMemberHandler.MY_CHAT_MEMBER))