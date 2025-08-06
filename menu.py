from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Update
from telegram.ext import CallbackQueryHandler, filters, MessageHandler, ContextTypes
from database import (
    get_link_settings, set_link_settings, get_welcome, set_welcome, delete_welcome,
    get_rules, set_rules, delete_rules, get_captcha_settings,
    set_captcha_settings, get_farewell, set_farewell, delete_farewell,
    get_rss_topic, list_rss_feeds as db_list_rss_feeds, remove_rss_feed,
    get_topic_owners, is_daily_stats_enabled, set_daily_stats,
    get_group_language, set_group_language, get_registered_groups
)
from access import get_visible_groups
from statistic import stats_command, export_stats_csv_command
from utils import clean_delete_accounts_for_chat, tr
from translator import translate_hybrid
from user_manual import HELP_TEXT
import logging, re

logger = logging.getLogger(__name__)

# Alias für alte get_lang-Aufrufe
get_lang = get_group_language

# Sprachoptionen
LANGUAGES = {
    'de': 'Deutsch', 'en': 'English', 'es': 'Español',
    'fr': 'Français', 'it': 'Italiano', 'ru': 'Русский'
}

async def show_group_menu(*, query=None, message=None, chat_id: int, context):
    # Immer ausgewählte Gruppe speichern
    context.user_data['selected_chat_id'] = chat_id

    lang = get_group_language(chat_id) or 'de'
    status = tr('Aktiv', lang) if is_daily_stats_enabled(chat_id) else tr('Inaktiv', lang)

    buttons = [
        [InlineKeyboardButton(tr('Begrüßung', lang), callback_data=f"{chat_id}_welcome"),
         InlineKeyboardButton(tr('🔐 Captcha', lang), callback_data=f"{chat_id}_captcha")],
        [InlineKeyboardButton(tr('Regeln', lang), callback_data=f"{chat_id}_rules"),
         InlineKeyboardButton(tr('Abschied', lang), callback_data=f"{chat_id}_farewell")],
        [InlineKeyboardButton(tr('🔗 Linksperre', lang), callback_data=f"{chat_id}_linkprot"),
         InlineKeyboardButton(tr('📰 RSS', lang), callback_data=f"{chat_id}_rss")],
        [InlineKeyboardButton(tr('🗑 Bereinigen', lang), callback_data=f"{chat_id}_clean_delete"),
         InlineKeyboardButton(tr('📊 Statistiken', lang), callback_data=f"{chat_id}_stats")],
        [InlineKeyboardButton(tr('📥 Export CSV', lang), callback_data=f"{chat_id}_stats_export"),
         InlineKeyboardButton(f"📊 Tagesreport {status}", callback_data=f"{chat_id}_toggle_stats")],
        [InlineKeyboardButton(tr('✍️ Mood-Frage ändern', lang), callback_data=f"{chat_id}_edit_mood_q"),
         InlineKeyboardButton(tr('🌐 Sprache', lang), callback_data=f"{chat_id}_language")],
        [InlineKeyboardButton(tr('📖 Handbuch', lang), callback_data="help"),
         InlineKeyboardButton(tr('🔄 Gruppe wechseln', lang), callback_data="group_select")]
    ]
    title = tr('🔧 Gruppe verwalten – wähle eine Funktion:', lang)
    markup = InlineKeyboardMarkup(buttons)

    from telegram.error import BadRequest
    try:
        if query:  # Aufruf über Button
            await query.edit_message_text(title, reply_markup=markup)
        elif message:  # Aufruf über /menu
            await message.reply_text(title, reply_markup=markup)
    except BadRequest as e:
        if 'Message is not modified' in str(e):
            if query:
                await query.answer()


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # 1) Gruppen-Auswahl starten
    if data == 'group_select':
        from database import get_registered_groups
        from access import get_visible_groups

        all_groups = get_registered_groups()
        groups = await get_visible_groups(query.from_user.id, context.bot, all_groups)

        if not groups:
            return await query.edit_message_text("🚫 Keine Gruppen gefunden, in denen du Admin bist.")

        buttons = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid, title in groups]
        return await query.edit_message_text('🔧 Wähle eine Gruppe:', reply_markup=InlineKeyboardMarkup(buttons))

    # 2) Auf Gruppe wechseln
    if data.startswith('group_'):
        _, id_str = data.split('_', 1)
        if id_str.isdigit():
            cid = int(id_str)
            context.user_data['selected_chat_id'] = cid
            return await show_group_menu(query=query, chat_id=cid, context=context)

    # 3) Numerische Callback-Pattern
    parts = data.split('_', 2)
    if not parts[0].isdigit():
        # Fallback zurück ins Hauptmenü
        cid = context.user_data.get('selected_chat_id')
        if cid:
            return await show_group_menu(query=query, chat_id=cid, context=context)
        return
    chat_id = int(parts[0])
    func = parts[1] if len(parts) > 1 else None
    action = parts[2] if len(parts) > 2 else None
    lang = get_group_language(chat_id)

    # 4) Tagesstatistiken Umschalten
    if func == 'toggle_stats':
        cur = is_daily_stats_enabled(chat_id)
        set_daily_stats(chat_id, not cur)
        await query.answer(tr(f"Tagesstatistik {'aktiviert' if not cur else 'deaktiviert'}", lang), show_alert=True)
        return await show_group_menu(query=query, chat_id=chat_id, context=context)

    # 5) Linksperre-Menü
    if func == "linkprot" and action is None:
        prot_on, warn_on, warn_text, except_on = get_link_settings(chat_id)
        kb = [
            [InlineKeyboardButton(f"{'✅' if prot_on else '☐'} {tr('Linkschutz aktiv', lang)}",
                                  callback_data=f"{chat_id}_linkprot_toggle")],
            [InlineKeyboardButton(f"{'✅' if warn_on else '☐'} {tr('Warn-Text senden', lang)}",
                                  callback_data=f"{chat_id}_linkprot_warn_toggle")],
            [InlineKeyboardButton(tr('Warn-Text bearbeiten', lang), callback_data=f"{chat_id}_linkprot_edit")],
            [InlineKeyboardButton(f"{'✅' if except_on else '☐'} {tr('Ausnahmen (Settopic)', lang)}",
                                  callback_data=f"{chat_id}_linkprot_exc_toggle")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{chat_id}")],
        ]
        return await query.edit_message_text(tr('🔧 Linksperre-Einstellungen:', lang), reply_markup=InlineKeyboardMarkup(kb))

    # 6) Aktionen der Linksperre
    if func == "linkprot":
        prot_on, warn_on, warn_text, except_on = get_link_settings(chat_id)
        if action == "toggle":
            set_link_settings(chat_id, protection=not prot_on)
            await query.answer(tr(f"Linkschutz {'aktiviert' if not prot_on else 'deaktiviert'}", lang), show_alert=True)
        elif action == "warn_toggle":
            set_link_settings(chat_id, warning_on=not warn_on)
            await query.answer(tr(f"Warn-Text {'aktiviert' if not warn_on else 'deaktiviert'}", lang), show_alert=True)
        elif action == "exc_toggle":
            set_link_settings(chat_id, exceptions_on=not except_on)
            await query.answer(tr(f"Ausnahmen {'aktiviert' if not except_on else 'deaktiviert'}", lang), show_alert=True)
        elif action == "edit":
            context.user_data['awaiting_link_warn'] = True
            context.user_data['link_warn_group'] = chat_id
            return await query.message.reply_text(tr("Sende jetzt deinen neuen Warn-Text:", lang),
                                                  reply_markup=ForceReply(selective=True))
        return await show_group_menu(query=query, chat_id=chat_id, context=context)

    # 7) Mood-Frage bearbeiten
    if func == "edit_mood_q":
        context.user_data['awaiting_mood_question'] = True
        context.user_data['mood_group_id'] = chat_id
        return await query.message.reply_text(tr('Bitte sende deine neue Mood-Frage:', lang),
                                              reply_markup=ForceReply(selective=True))

    if re.match(r'^\d+_stats_export$', data):
        return await export_stats_csv_command(update, context)

    if re.match(r'^\d+_stats$', data):
        context.user_data['stats_group_id'] = int(data.split('_')[0])
        return await stats_command(update, context)

    # Handbuch
    if data == 'help':
        cid = context.user_data.get('selected_chat_id')
        lang = get_group_language(cid) or 'de'
        translated = translate_hybrid(HELP_TEXT, target_lang=lang)
        path = f'user_manual_{lang}.md'
        with open(path, 'w', encoding='utf-8') as f:
            f.write(translated)
        await query.message.reply_document(document=open(path, 'rb'), filename=f'Handbuch_{lang}.md')
        return await show_group_menu(query=query, chat_id=cid, context=context)

    # 3) Submenüs: welcome, rules, farewell, rss, exceptions, captcha
    # Splitting data into cid, func, [action]
    parts = data.split('_',2)
    if len(parts) >= 2 and parts[1] in ('welcome','rules','farewell','rss','exceptions','captcha','language'):
        cid = int(parts[0])
        func = parts[1]
        sub = parts[2] if len(parts)>=3 else None
        lang = get_lang(cid)
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        chat_id = chat.id
        back = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Zurück', callback_data=f'group_{cid}')]])

        # Welcome/Rules/Farewell Menü
        if func in ('welcome','rules','farewell') and not sub:
            kb = [
                [InlineKeyboardButton(tr('Bearbeiten', lang), callback_data=f"{cid}_{func}_edit"),
                 InlineKeyboardButton(tr('Anzeigen', lang), callback_data=f"{cid}_{func}_show")],
                [InlineKeyboardButton(tr('Löschen', lang), callback_data=f"{cid}_{func}_delete")],
                [InlineKeyboardButton(tr('⬅ Hauptmenü', lang), callback_data=f"group_{cid}")]
            ]
            text = tr(f"⚙️ {func.capitalize()} verwalten:", lang)
            return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

        # RSS Menü
        if func == 'rss' and not sub:
            kb = [
                [InlineKeyboardButton(tr('Auflisten', lang), callback_data=f"{cid}_rss_list"),
                 InlineKeyboardButton(tr('Feed hinzufügen', lang), callback_data=f"{cid}_rss_setrss")],
                [InlineKeyboardButton(tr('Stoppen', lang), callback_data=f"{cid}_rss_stop")],
                [InlineKeyboardButton(tr('⬅ Hauptmenü', lang), callback_data=f"group_{cid}")]
            ]
            text = tr('📰 RSS verwalten', lang)
            return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

        # Exceptions Menü
        if func == 'exceptions' and not sub:
            admins = await context.bot.get_chat_administrators(cid)
            names = [f"@{a.user.username}" if a.user.username else a.user.first_name for a in admins]
            owners = get_topic_owners(cid)
            text = '🔓 Ausnahmen der Link-Sperre:\n' + \
                   f"- Admins: {', '.join(names)}\n" + \
                   f"- Themenbesitzer: {', '.join(str(o) for o in owners) or '(keine)'}"
            return await query.edit_message_text(text, reply_markup=back)

        # Captcha Menü
        if func == 'captcha' and not sub:
            en, ctype, behavior = get_captcha_settings(cid) # pyright: ignore[reportCallIssue]
            kb = [
                [InlineKeyboardButton(f"{'✅ ' if en else ''}{tr('Aktiviert', lang) if en else tr('Deaktiviert', lang)}", callback_data=f"{cid}_captcha_toggle")],
                [InlineKeyboardButton(f"{'✅ ' if ctype=='button' else ''}{tr('Button', lang)}", callback_data=f"{cid}_captcha_type_button"),
                 InlineKeyboardButton(f"{'✅ ' if ctype=='math' else ''}{tr('Rechenaufgabe', lang)}", callback_data=f"{cid}_captcha_type_math")],
                [InlineKeyboardButton(f"{'✅ ' if behavior=='kick' else ''}{tr('Kick', lang)}", callback_data=f"{cid}_captcha_behavior_kick"),
                 InlineKeyboardButton(f"{'✅ ' if behavior=='timeout' else ''}{tr('Timeout', lang)}", callback_data=f"{cid}_captcha_behavior_timeout")],
                [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
            ]
            return await query.edit_message_text(tr('🔐 Captcha-Einstellungen', lang), reply_markup=InlineKeyboardMarkup(kb))

        # Captcha-Unterbefehle verarbeiten
        if func == 'captcha' and sub == 'toggle':
            # Status umschalten
            en, ctype, behavior = get_captcha_settings(cid)
            set_captcha_settings(cid, not en, ctype, behavior)
            await query.answer(
                tr(f"Captcha {'aktiviert' if not en else 'deaktiviert'}", lang),
                show_alert=True
            )
            return await show_group_menu(query=query, chat_id=cid, context=context)

        if func == 'captcha' and sub in ('type_button', 'type_math'):
            # Typ ändern
            en, ctype, behavior = get_captcha_settings(cid)
            new_type = sub.split('_', 1)[1]  # 'button' oder 'math'
            set_captcha_settings(cid, en, new_type, behavior)
            await query.answer(tr("Captcha-Typ geändert", lang), show_alert=True)
            return await show_group_menu(query=query, chat_id=cid, context=context)

        if func == 'captcha' and sub in ('behavior_kick', 'behavior_timeout'):
            # Verhalten ändern
            en, ctype, behavior = get_captcha_settings(cid)
            new_behavior = sub.split('_', 1)[1]  # 'kick' oder 'timeout'
            set_captcha_settings(cid, en, ctype, new_behavior)
            await query.answer(tr("Captcha-Verhalten geändert", lang), show_alert=True)
            return await show_group_menu(query=query, chat_id=cid, context=context)

    # 4) Detail-Handler Actions (edit/show/delete etc.)
    parts = data.split('_')
    if len(parts) == 3:
        cid, func, action = parts
        cid = int(cid)
        back = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Zurück', callback_data=f"{cid}_{func}")]])
        get_map = {'welcome': get_welcome, 'rules': get_rules, 'farewell': get_farewell}
        set_map = {'welcome': set_welcome, 'rules': set_rules, 'farewell': set_farewell}
        del_map = {'welcome': delete_welcome, 'rules': delete_rules, 'farewell': delete_farewell}

        # Inhalte zeigen oder löschen oder editieren
        if action == 'show' and func in get_map:
            rec = get_map[func](cid) # pyright: ignore[reportCallIssue]
            text = rec[1] if rec else f"Keine {func}-Nachricht gesetzt."
            return await query.edit_message_text(text, reply_markup=back)
        if action == 'delete' and func in del_map:
            del_map[func](cid) # type: ignore
            await query.answer(tr(f"✅ {func.capitalize()} gelöscht.", get_lang(cid)), show_alert=True)
            return await query.edit_message_text(tr(f"{func.capitalize()} entfernt.", get_lang(cid)), reply_markup=back) # type: ignore
        if action == 'edit' and func in set_map:
            context.user_data['last_edit'] = (cid, f"{func}_edit")
            return await query.edit_message_text(f"✏️ Sende nun das neue {func}:", reply_markup=back)

    # 5) RSS-Detail
    if data.endswith('_rss_setrss'):
        cid = int(data.split('_',1)[0])
        if not get_rss_topic(cid): # pyright: ignore[reportCallIssue]
            await query.answer('❗ Kein RSS-Topic gesetzt.', show_alert=True)
            return await show_group_menu(query=query, chat_id=cid, context=context)
        context.user_data.update(awaiting_rss_url=True, rss_group_id=cid)
        await query.answer()
        return await query.edit_message_text('➡ Bitte sende die RSS-URL:', reply_markup=ForceReply(selective=True))
    if data.endswith('_rss_list'):
        cid = int(data.split('_',1)[0])
        feeds = db_list_rss_feeds(cid) # pyright: ignore[reportCallIssue]
        text = 'Keine RSS-Feeds.' if not feeds else 'Aktive Feeds:\n' + '\n'.join(feeds)
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Zurück', callback_data=f'group_{cid}')]]))
    if data.endswith('_rss_stop'):
        cid = int(data.split('_',1)[0])
        remove_rss_feed(cid) # type: ignore # pyright: ignore[reportCallIssue]
        await query.answer('✅ RSS gestoppt', show_alert=True)
        return await show_group_menu(query=query, chat_id=cid, context=context)

    # 6) Gelöschte Accounts entfernen
    if data.endswith('_clean_delete'):
        cid = int(data.split('_',1)[0])
        await query.answer('⏳ Bereinige…')
        removed = await clean_delete_accounts_for_chat(cid, context.bot)
        text = f"✅ {removed} Accounts entfernt."
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Zurück', callback_data=f'group_{cid}')]]))

    # 7) Sprache setzen
    if re.match(r'^\d+_language$', data):
        cid = int(data.split('_')[0])
        cur = get_lang(cid) or 'de'
        kb = [[InlineKeyboardButton(f"{'✅ ' if c==cur else ''}{n}", callback_data=f"{cid}_setlang_{c}")] for c,n in LANGUAGES.items()]
        kb.append([InlineKeyboardButton('↩️ Zurück', callback_data=f'group_{cid}')])
        return await query.edit_message_text(tr('🌐 Wähle Sprache:', cur), reply_markup=InlineKeyboardMarkup(kb))
    if '_setlang_' in data:
        cid,_,lang = data.partition('_setlang_')[::2]
        cid = int(cid)
        set_group_language(cid, lang)
        await query.answer(tr(f"Gruppensprache gesetzt: {LANGUAGES[lang]}", lang), show_alert=True)
        return await show_group_menu(query=query, chat_id=cid, context=context)

    # Fallback: Hauptmenü
    cid = context.user_data.get('selected_chat_id')
    if cid:
        return await show_group_menu(query=query, chat_id=cid, context=context)

    
# /menu 

def register_menu(app):

    app.add_handler(CallbackQueryHandler(menu_callback))
