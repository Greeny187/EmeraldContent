from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, ForceReply
from telegram.ext import CallbackQueryHandler
from database import (
    get_registered_groups,
    get_welcome, set_welcome, delete_welcome,
    list_members,
    get_rules, set_rules, delete_rules,
    get_farewell, set_farewell, delete_farewell,
    list_rss_feeds as db_list_rss_feeds, remove_rss_feed,
    get_topic_owners,
    is_daily_stats_enabled,  # *** ÄNDERUNG: hinzugefügt für Toggle-Logik ***
    set_daily_stats,         # *** ÄNDERUNG: hinzugefügt für Toggle-Logik ***
    get_mood_question       # *** ÄNDERUNG: hinzugefügt für dynamische Mood-Frage ***
)
from utils import is_deleted_account, clean_delete_accounts_for_chat
from user_manual import HELP_TEXT
import logging

logger = logging.getLogger(__name__)

async def show_group_menu(query_or_update, chat_id: int):
    question = get_mood_question(chat_id)
    keyboard = [
        [InlineKeyboardButton("Begrüßung",    callback_data=f"{chat_id}_welcome")],
        [InlineKeyboardButton("Regeln",       callback_data=f"{chat_id}_rules")],
        [InlineKeyboardButton("Farewell",     callback_data=f"{chat_id}_farewell")],
        [InlineKeyboardButton("Linksperre",   callback_data=f"{chat_id}_exceptions")],
        [InlineKeyboardButton("RSS",          callback_data=f"{chat_id}_rss")],
        [InlineKeyboardButton("🗑 Gelöschte Accounts entfernen", callback_data=f"{chat_id}_clean_delete")],
        [InlineKeyboardButton(
            "📊 Tagesstatistik " + ("Aktiv" if is_daily_stats_enabled(chat_id) else "Inaktiv"),
            callback_data=f"{chat_id}_toggle_stats"
        )],
        [InlineKeyboardButton("✍️ Mood-Frage ändern", callback_data=f"{chat_id}_edit_mood_q")],
        [InlineKeyboardButton("📖 Handbuch",  callback_data="help")],
    ]
    text = "🔧 Gruppe verwalten – wähle eine Funktion:"
    markup = InlineKeyboardMarkup(keyboard)
    if hasattr(query_or_update, "callback_query"):
        await query_or_update.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await query_or_update.message.reply_text(text, reply_markup=markup)

async def menu_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("group_"):
        chat_id = int(data.split("_",1)[1])
        context.user_data["selected_chat_id"] = chat_id
        return await show_group_menu(query, chat_id)

    if data.endswith("_toggle_stats"):
        chat_id = int(data.split("_",1)[0])
        current = is_daily_stats_enabled(chat_id)
        set_daily_stats(chat_id, not current)
        await query.answer(f"Tagesstatistik {'aktiviert' if not current else 'deaktiviert'}", show_alert=True)
        return await show_group_menu(query, chat_id)

    if data.endswith("_edit_mood_q"):
        chat_id = int(data.split("_",1)[0])
        context.user_data["awaiting_mood_question"] = True
        context.user_data["mood_group_id"] = chat_id
        return await query.message.reply_text(
            "Bitte sende deine neue Mood-Frage:", reply_markup=ForceReply(selective=True)
        )

    if data == "help":
        return await query.message.reply_text(HELP_TEXT, parse_mode='Markdown')

    # Sub-Menüs: welcome, rules, farewell, rss, exceptions
    parts = data.split("_", 1)
    if len(parts) == 2 and parts[1] in ("welcome", "rules", "farewell", "rss", "exceptions"):
        chat_id_str, func = parts
        chat_id = int(chat_id_str)
        back_main = InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Hauptmenü", callback_data=f"group_{chat_id}")]])

        if func in ("welcome", "rules", "farewell"):
            kb = [
                [InlineKeyboardButton("Bearbeiten", callback_data=f"{chat_id}_{func}_edit")],
                [InlineKeyboardButton("Anzeigen",   callback_data=f"{chat_id}_{func}_show")],
                [InlineKeyboardButton("Löschen",    callback_data=f"{chat_id}_{func}_delete")],
                [InlineKeyboardButton("⬅ Hauptmenü", callback_data=f"group_{chat_id}")],
            ]
            text = f"⚙ {func.capitalize()} verwalten:"
            markup = InlineKeyboardMarkup(kb)
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(text, reply_markup=markup)
            else:
                await query.edit_message_text(text, reply_markup=markup)
            return

        if func == "rss":
            kb = [
                [InlineKeyboardButton("Auflisten", callback_data=f"{chat_id}_rss_list")],
                [InlineKeyboardButton("Stoppen",   callback_data=f"{chat_id}_rss_stop")],
                [InlineKeyboardButton("⬅ Hauptmenü", callback_data=f"group_{chat_id}")],
            ]
            text = "📰 RSS verwalten"
            markup = InlineKeyboardMarkup(kb)
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(text, reply_markup=markup)
            else:
                await query.edit_message_text(text, reply_markup=markup)
            return

        if func == "exceptions":
            admins = await context.bot.get_chat_administrators(chat_id)
            admin_names = [f"@{a.user.username}" if a.user.username else a.user.first_name for a in admins]
            owner = next((a for a in admins if a.status == "creator"), None)
            owner_name = f"@{owner.user.username}" if owner and owner.user.username else (owner.user.first_name if owner else "–")
            topic_ids = get_topic_owners(chat_id)
            topic_names = []
            for uid in topic_ids:
                try:
                    m = await context.bot.get_chat_member(chat_id, uid)
                    topic_names.append(f"@{m.user.username}" if m.user.username else m.user.first_name)
                except:
                    continue
            lines = ["🔓 Ausnahmen der Link-Sperre:"]
            lines.append(f"- Administratoren: {', '.join(admin_names)}")
            lines.append(f"- Inhaber: {owner_name}")
            lines.append(f"- Themenbesitzer: {', '.join(topic_names) if topic_names else '(keine)'}")
            text = "\n".join(lines)
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(text, reply_markup=back_main)
            else:
                await query.edit_message_text(text, reply_markup=back_main)
            return

    # 3) Detail-Handler (Action-Blocks) – unverändert
    parts_full = data.split("_")
    if len(parts_full) == 3:
        chat_id, func, action = parts_full
        chat_id = int(chat_id)
        back_func = InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Zurück", callback_data=f"{chat_id}_{func}")]])

        # Welcome/Rules/Farewell mappings
        get_map = {"welcome": get_welcome, "rules": get_rules, "farewell": get_farewell}
        set_map = {"welcome": set_welcome, "rules": set_rules, "farewell": set_farewell}
        del_map = {"welcome": delete_welcome, "rules": delete_rules, "farewell": delete_farewell}

        # Show
        if action == "show" and func in get_map:
            rec = get_map[func](chat_id)
            if not rec:
                msg = f"Keine {func}-Nachricht gesetzt."
                if query.message.photo or query.message.caption:
                    await query.edit_message_caption(msg, reply_markup=back_func)
                else:
                    await query.edit_message_text(msg, reply_markup=back_func)
            else:
                pid, txt = rec
                if pid:
                    await query.edit_message_media(InputMediaPhoto(pid, caption=txt or ""), reply_markup=back_func)
                else:
                    if query.message.photo or query.message.caption:
                        await query.edit_message_caption(txt or "(kein Text)", reply_markup=back_func)
                    else:
                        await query.edit_message_text(txt or "(kein Text)", reply_markup=back_func)
            return

        # Delete
        if action == "delete" and func in del_map:
            del_map[func](chat_id)
            msg = f"✅ {func.capitalize()} gelöscht."
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(msg, reply_markup=back_func)
            else:
                await query.edit_message_text(msg, reply_markup=back_func)
            return

        # Edit
        if action == "edit" and func in set_map:
            context.user_data["last_edit"] = (chat_id, f"{func}_edit")
            prompt = f"✏️ Sende nun das neue {func}"
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(prompt, reply_markup=back_func)
            else:
                await query.edit_message_text(prompt, reply_markup=back_func)
            return

        # RSS List
        if func == "rss" and action == "list":
            feeds = db_list_rss_feeds(chat_id)
            if not feeds:
                text = "Keine RSS-Feeds gesetzt."
            else:
                text = "Aktive RSS-Feeds:\n" + "\n".join(f"- {url} (Topic {tid})" for url, tid in feeds)
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(text, reply_markup=back_func)
            else:
                await query.edit_message_text(text, reply_markup=back_func)
            return

        # RSS Stop
        if func == "rss" and action == "stop":
            remove_rss_feed(chat_id)
            msg = "✅ Alle RSS-Feeds entfernt."
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(msg, reply_markup=back_func)
            else:
                await query.edit_message_text(msg, reply_markup=back_func)
            return 

        # Klick auf "Gelöschte Accounts entfernen"
        if data.endswith("_clean_delete"):
            chat_id = int(data.split("_",1)[0])
            # Anzahl ermitteln
            count = 0
            for uid in list_members(chat_id):
                member = await context.bot.get_chat_member(chat_id, uid)
                if is_deleted_account(member):
                    count += 1

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Entfernen", callback_data=f"{chat_id}_confirm_clean")],
                [InlineKeyboardButton("❌ Abbrechen", callback_data=f"{chat_id}_cancel_clean")]
            ])
            await query.edit_message_text(
                f"In Gruppe {chat_id} würden *{count}* gelöschte Accounts entfernt. Fortfahren?",
                parse_mode="Markdown",
                reply_markup=kb
            )
            return

        # Bestätigung drücken
        if data.endswith("_confirm_clean"):
            chat_id = int(data.split("_",1)[0])
            removed_count = await clean_delete_accounts_for_chat(chat_id, context.bot)
            await query.edit_message_text(f"✅ {removed_count} Accounts entfernt.")
            return

        # Abbruch
        if data.endswith("_cancel_clean"):
            await query.edit_message_text("❌ Vorgang abgebrochen.")
            return
    
# /menu 

def register_menu(app):
    app.add_handler(CallbackQueryHandler(menu_callback))
    
