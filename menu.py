from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, ForceReply
from telegram.ext import CallbackQueryHandler
from database import (
    get_registered_groups,
    get_welcome, set_welcome, delete_welcome,
    list_members, set_group_language, get_group_language,
    get_rules, set_rules, delete_rules,
    get_farewell, set_farewell, delete_farewell,
    list_rss_feeds as db_list_rss_feeds, remove_rss_feed,
    get_topic_owners,
    is_daily_stats_enabled,  # *** ÄNDERUNG: hinzugefügt für Toggle-Logik ***
    set_daily_stats,         # *** ÄNDERUNG: hinzugefügt für Toggle-Logik ***
    get_mood_question       # *** ÄNDERUNG: hinzugefügt für dynamische Mood-Frage ***
)
from utils import clean_delete_accounts_for_chat, tr
from user_manual import HELP_TEXT
from access import get_visible_groups
import logging

logger = logging.getLogger(__name__)

# Sprachoptionen
LANGUAGES = {
    'de': 'Deutsch',
    'en': 'English',
    'es': 'Español',
    'fr': 'Français'
}

async def show_group_menu(query_or_update, context, chat_id: int):
    question = get_mood_question(chat_id)
    # Aktuelle Gruppensprache oder Standard 'de'
    lang = get_group_language(chat_id) or 'de'
    # Tasten-Definition
    buttons = [
        ("Begrüßung",    f"{chat_id}_welcome"),
        ("Regeln",       f"{chat_id}_rules"),
        ("Farewell",     f"{chat_id}_farewell"),
        ("Linksperre",   f"{chat_id}_exceptions"),
        ("RSS",          f"{chat_id}_rss"),
        ("🗑 Gelöschte Accounts entfernen", f"{chat_id}_clean_delete"),
        (f"📊 Tagesstatistik {'Aktiv' if is_daily_stats_enabled(chat_id) else 'Inaktiv'}", f"{chat_id}_toggle_stats"),
        ("✍️ Mood-Frage ändern", f"{chat_id}_edit_mood_q"),
        ("🌐 Sprache", f"{chat_id}_language"),
        ("📖 Handbuch",  "help"),
        ("🔄 Gruppe wechseln", "group_select")
    ]
    # Beschriftung übersetzen
    title = tr("🔧 Gruppe verwalten – wähle eine Funktion:", lang)
    # Buttons übersetzen
    keyboard = [
        [InlineKeyboardButton(tr(text, lang), callback_data=cd)]
        for text, cd in buttons
    ]
    markup = InlineKeyboardMarkup(keyboard)

    # Universelle Behandlung je nach Typ
    if hasattr(query_or_update, "edit_message_text"):
        await query_or_update.edit_message_text(title, reply_markup=markup)
    elif getattr(query_or_update, "callback_query", None) is not None:
        await query_or_update.callback_query.edit_message_text(title, reply_markup=markup)
    elif hasattr(query_or_update, "reply_text"):  # plain Message-Objekt
        await query_or_update.reply_text(title, reply_markup=markup)
    elif hasattr(query_or_update, "message"):  # plain Update mit Message
        await query_or_update.message.reply_text(title, reply_markup=markup)
    else:
        raise TypeError("❌ Ungültiger Objekttyp für show_group_menu()")

async def menu_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "group_select":
        context.user_data.pop("selected_chat_id", None)
        all_groups = get_registered_groups()
        visible = await get_visible_groups(update.effective_user.id, context.bot, all_groups)
        if not visible:
            return await query.message.reply_text(tr("🚫 Keine Gruppen sichtbar.", 'de'))
        kb = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid, title in visible]
        return await query.message.reply_text(tr("🔧 Wähle eine andere Gruppe:", 'de'), reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("group_"):
        chat_id = int(data.split("_",1)[1])
        all_groups = get_registered_groups()
        visible_groups = await get_visible_groups(update.effective_user.id, context.bot, all_groups)
        if chat_id not in [cid for cid, _ in visible_groups]:
            return await query.message.reply_text("🚫 Du hast keinen Zugriff auf diese Gruppe.")
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
                [InlineKeyboardButton("Feed hinzufügen", callback_data=f"{chat_id}_rss_setrss")],
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

        # RSS «Feed hinzufügen» aus Menü
        if func == "rss" and action == "setrss":
            # Kennzeichnen, dass wir auf die URL warten
            context.user_data["awaiting_rss_url"] = True
            context.user_data["rss_group_id"] = int(chat_id)
            return await query.message.reply_text(
                "➡ Bitte sende jetzt die RSS-URL für diese Gruppe:",
                reply_markup=ForceReply(selective=True)
            )

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

        # Klick auf "🗑 Gelöschte Accounts entfernen"
        if data.endswith("_clean_delete"):
            chat_id = int(data.split("_", 1)[0])

            # 1) Callback sofort bestätigen, damit er nicht abläuft
            await query.answer(text="⏳ Entferne gelöschte Accounts…")

            # 2) Cleanup ausführen und Zahl der entfernten Accounts ermitteln
            removed_count = await clean_delete_accounts_for_chat(chat_id, context.bot)

            # 3) Ergebnis in der Message anzeigen (und Tastatur beibehalten)
            await query.edit_message_text(
                text=f"✅ In Gruppe {chat_id} wurden {removed_count} gelöschte Accounts entfernt.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ Zurück", callback_data=f"group_{chat_id}")]
                ])
            )
            return
        
        # Sprache-Menü anzeigen
        if data.endswith("_language"):
            chat_id = int(data.split("_",1)[0])
            current = get_group_language(chat_id) or 'de'
            kb = [
                [InlineKeyboardButton(
                    f"{'✅ ' if code==current else ''}{name}",
                    callback_data=f"{chat_id}_setlang_{code}"
                )]
                for code, name in LANGUAGES.items()
            ]
            kb.append([InlineKeyboardButton(tr("↩️ Zurück", current), callback_data=f"group_{chat_id}")])
            await query.edit_message_text(tr("🌐 Wähle Sprache:", current), reply_markup=InlineKeyboardMarkup(kb))
            return

        # Sprache setzen
        if "_setlang_" in data:
            parts = data.split("_")
            chat_id = int(parts[0])
            lang = parts[-1]
            # In der DB speichern
            set_group_language(chat_id, lang)
            language_name = LANGUAGES.get(lang, lang)
            await query.answer(f"Gruppensprache gesetzt: {language_name}", show_alert=True)
            return await show_group_menu(query, context, chat_id)
        
        # Fallback: Wenn Callback nur die Chat-ID mit Prefix enthält
        if data.startswith("group_"):
            chat_id = int(data.split("_", 1)[1])
            context.user_data["selected_chat_id"] = chat_id
            return await show_group_menu(query, context, chat_id)
    
# /menu 

def register_menu(app):
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r'^(?!(mood_)).*'))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^cleanup$"))
