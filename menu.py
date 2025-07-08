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
    'fr': 'Français',
    'it': 'Italiano',
    'ru': 'Русский'
}

# Manuelle Übersetzungs-Overrides für Menü-Labels
MENU_OVERRIDES = {
    'Linksperre': {
        'en': 'Link restrictions',
        'es': 'Bloqueo de enlaces',
        'fr': 'Blocage des liens'
    }
}

def translate_label(text: str, lang: str) -> str:
    """
    Übersetzt Menü-Label: nutzt manuelle Overrides, ansonsten tr().
    """
    if text in MENU_OVERRIDES and lang in MENU_OVERRIDES[text]:
        return MENU_OVERRIDES[text][lang]
    return tr(text, lang)

async def show_group_menu(query_or_update, chat_id: int):
    # Sprache der Gruppe
    lang = get_group_language(chat_id) or 'de'
    # Menü-Buttons
    keyboard = [
        [InlineKeyboardButton(tr('Begrüßung', lang), callback_data=f"{chat_id}_welcome")],
        [InlineKeyboardButton(tr('Regeln', lang), callback_data=f"{chat_id}_rules")],
        [InlineKeyboardButton(tr('Abschied', lang), callback_data=f"{chat_id}_farewell")],
        [InlineKeyboardButton(tr('Linksperre', lang), callback_data=f"{chat_id}_exceptions")],
        [InlineKeyboardButton(tr('RSS', lang), callback_data=f"{chat_id}_rss")],
        [InlineKeyboardButton(tr('🗑 Gelöschte Accounts entfernen', lang), callback_data=f"{chat_id}_clean_delete")],
        [InlineKeyboardButton(
            tr('📊 Tagesstatistik {status}', lang).format(status=tr('Aktiv', lang) if is_daily_stats_enabled(chat_id) else tr('Inaktiv', lang)),
            callback_data=f"{chat_id}_toggle_stats"
        )],
        [InlineKeyboardButton(tr('✍️ Mood-Frage ändern', lang), callback_data=f"{chat_id}_edit_mood_q")],
        [InlineKeyboardButton(tr('🌐 Sprache', lang), callback_data=f"{chat_id}_language")],
        [InlineKeyboardButton(tr('📖 Handbuch', lang), callback_data="help")],
        [InlineKeyboardButton(tr('🔄 Gruppe wechseln', lang), callback_data="group_select")]
    ]
    title = tr('🔧 Gruppe verwalten – wähle eine Funktion:', lang)
    markup = InlineKeyboardMarkup(keyboard)

    # Sende oder aktualisiere Nachricht
    if hasattr(query_or_update, 'edit_message_text'):
        await query_or_update.edit_message_text(title, reply_markup=markup)
    elif hasattr(query_or_update, 'message'):
        await query_or_update.message.reply_text(title, reply_markup=markup)
    else:
        await query_or_update.reply_text(title, reply_markup=markup)

async def menu_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    # EINMALIG: Gruppensprache abrufen
    lang = get_group_language(chat_id) or 'de'
    
    # Gruppenauswahl
    if data == 'group_select':
        context.user_data.pop('selected_chat_id', None)
        groups = get_registered_groups()
        visible = await get_visible_groups(update.effective_user.id, context.bot, groups)
        kb = [[InlineKeyboardButton(name, callback_data=f"group_{gid}")] for gid, name in visible]
        return await query.message.reply_text(tr('🔧 Wähle eine Gruppe:', 'de'), reply_markup=InlineKeyboardMarkup(kb))

    # Gruppe geladen
    if data.startswith('group_'):
        chat_id = int(data.split('_')[1])
        context.user_data['selected_chat_id'] = chat_id
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
                [InlineKeyboardButton(translate_label('Bearbeiten', lang), callback_data=f"{chat_id}_{func}_edit")],
                [InlineKeyboardButton(translate_label('Anzeigen', lang), callback_data=f"{chat_id}_{func}_show")],
                [InlineKeyboardButton(translate_label('Löschen', lang), callback_data=f"{chat_id}_{func}_delete")],
                [InlineKeyboardButton(translate_label('⬅ Hauptmenü', lang), callback_data=f"group_{chat_id}")]
            ]
            text = tr(f"⚙ {func.capitalize()} verwalten:", lang)
            markup = InlineKeyboardMarkup(kb)
            if query.message.photo or query.message.caption:
                await query.edit_message_caption(text, reply_markup=markup)
            else:
                await query.edit_message_text(text, reply_markup=markup)
            return

        if func == "rss":
            kb = [
                [InlineKeyboardButton(translate_label('Auflisten', lang),    callback_data=f"{chat_id}_rss_list")],
                [InlineKeyboardButton(translate_label('Feed hinzufügen', lang), callback_data=f"{chat_id}_rss_setrss")],
                [InlineKeyboardButton(translate_label('Stoppen', lang),      callback_data=f"{chat_id}_rss_stop")],
                [InlineKeyboardButton(translate_label('⬅ Hauptmenü', lang),   callback_data=f"group_{chat_id}")]
            ]
            text = tr('📰 RSS verwalten', lang)
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
        
    # Sprache-Submenu
    if data.endswith('_language'):
        chat_id = int(data.split('_')[0])
        current = get_group_language(chat_id) or 'de'
        kb = [[InlineKeyboardButton(f"{'✅ ' if code==current else ''}{name}", callback_data=f"{chat_id}_setlang_{code}")] for code, name in LANGUAGES.items()]
        kb.append([InlineKeyboardButton(tr('↩️ Zurück', current), callback_data=f"group_{chat_id}")])
        return await query.edit_message_text(tr('🌐 Wähle Sprache:', current), reply_markup=InlineKeyboardMarkup(kb))

    # Sprache setzen
    if '_setlang_' in data:
        parts = data.split('_')
        chat_id = int(parts[0])
        lang = parts[-1]
        set_group_language(chat_id, lang)
        await query.answer(tr(f'Gruppensprache gesetzt: {LANGUAGES[lang]}', lang), show_alert=True)
        return await show_group_menu(query, chat_id)

    # Fallback: zeige Menü erneut
    selected = context.user_data.get('selected_chat_id')
    if selected:
        return await show_group_menu(query, selected)
    
# /menu 

def register_menu(app):
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r'^(?!(mood_)).*'))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^cleanup$"))
