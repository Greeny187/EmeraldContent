from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, ForceReply, Update
from telegram.ext import CallbackQueryHandler, filters, MessageHandler
from database import (
    get_registered_groups, get_welcome, set_welcome, delete_welcome,
    get_rules, set_rules, delete_rules, get_captcha_settings,
    set_captcha_settings, get_farewell, set_farewell, delete_farewell,
    get_rss_topic, list_rss_feeds as db_list_rss_feeds, remove_rss_feed,
    get_topic_owners, is_daily_stats_enabled, set_daily_stats,
    get_group_language, set_group_language, get_group_language as get_lang
)
from statistic import stats_command, export_stats_csv_command, stats_dev_command
from utils import clean_delete_accounts_for_chat, tr
from translator import translate_hybrid
from user_manual import HELP_TEXT
from access import get_visible_groups
import logging, re

logger = logging.getLogger(__name__)

# Sprachoptionen
LANGUAGES = {
    'de': 'Deutsch', 'en': 'English', 'es': 'Español',
    'fr': 'Français', 'it': 'Italiano', 'ru': 'Русский'
}

# Menü-Oberfläche: Zwei Spalten
async def show_group_menu(query_or_update, chat_id: int):
    lang = get_group_language(chat_id) or 'de' # pyright: ignore[reportCallIssue]
    status = tr('Aktiv', lang) if is_daily_stats_enabled(chat_id) else tr('Inaktiv', lang)

    buttons = [
        [InlineKeyboardButton(tr('Begrüßung', lang), callback_data=f"{chat_id}_welcome"),
         InlineKeyboardButton(tr('🔐 Captcha', lang), callback_data=f"{chat_id}_captcha")],
        [InlineKeyboardButton(tr('Regeln', lang), callback_data=f"{chat_id}_rules"),
         InlineKeyboardButton(tr('Abschied', lang), callback_data=f"{chat_id}_farewell")],
        [InlineKeyboardButton(tr('Linksperre', lang), callback_data=f"{chat_id}_exceptions"),
         InlineKeyboardButton(tr('📰 RSS', lang), callback_data=f"{chat_id}_rss")],
        [InlineKeyboardButton(tr('🗑 Bereinigen', lang), callback_data=f"{chat_id}_clean_delete"),
         InlineKeyboardButton(tr('📊 Statistiken', lang), callback_data=f"{chat_id}_stats")],
        [InlineKeyboardButton(tr('📥 Export CSV', lang), callback_data=f"{chat_id}_stats_export"),
         InlineKeyboardButton(tr('📊 Tagesreport {status}', lang).format(status=status), callback_data=f"{chat_id}_toggle_stats")],
        [InlineKeyboardButton(tr('✍️ Mood-Frage ändern', lang), callback_data=f"{chat_id}_edit_mood_q"),
         InlineKeyboardButton(tr('🌐 Sprache', lang), callback_data=f"{chat_id}_language")],
        [InlineKeyboardButton(tr('📖 Handbuch', lang), callback_data="help"),
         InlineKeyboardButton(tr('🔄 Gruppe wechseln', lang), callback_data="group_select")]
    ]

    title = tr('🔧 Gruppe verwalten – wähle eine Funktion:', lang)
    markup = InlineKeyboardMarkup(buttons)

    if hasattr(query_or_update, 'edit_message_text'):
        await query_or_update.edit_message_text(title, reply_markup=markup)
    elif hasattr(query_or_update, 'message'):
        await query_or_update.message.reply_text(title, reply_markup=markup)
    else:
        await query_or_update.reply_text(title, reply_markup=markup)

# /menu Command
async def menu_command(update: Update, context):
    chat_id = update.effective_chat.id
    # Merke Gruppe für Help
    context.user_data['selected_chat_id'] = chat_id
    return await show_group_menu(update, chat_id)

async def menu_callback(update, context):
    query = update.callback_query
    data = query.data
    # 1) Gruppen-Auswahl
    if data == 'group_select':
        groups = get_visible_groups(context.bot, query.from_user.id)
        buttons = [[InlineKeyboardButton(str(cid), callback_data=f"group_{cid}")] for cid in groups]
        return await query.edit_message_text('Wähle Gruppe:', reply_markup=InlineKeyboardMarkup(buttons))
    if data.startswith('group_'):
        return await show_group_menu(query, int(data.split('_',1)[1]))

    # 2) Exakte Callback-Muster
    if re.match(r'^\d+_toggle_stats$', data):
        cid = int(data.split('_',1)[0])
        cur = is_daily_stats_enabled(cid)
        set_daily_stats(cid, not cur)
        await query.answer(tr(f"Tagesstatistik {'aktiviert' if not cur else 'deaktiviert'}", get_lang(cid)), show_alert=True)
        return await show_group_menu(query, cid)
    if re.match(r'^\d+_stats_export$', data):
        return await export_stats_csv_command(update, context)
    if re.match(r'^\d+_stats_dev$', data):
        return await stats_dev_command(update, context)
    if re.match(r'^\d+_stats$', data):
        context.user_data['stats_group_id'] = int(data.split('_',1)[0])
        return await stats_command(update, context)
    if re.match(r'^\d+_edit_mood_q$', data):
        cid = int(data.split('_',1)[0])
        context.user_data.update(awaiting_mood_question=True, mood_group_id=cid)
        return await query.message.reply_text('Bitte sende deine neue Mood-Frage:', reply_markup=ForceReply(selective=True))

    # Help-Handler: Handbuch als Datei mit Hybrid-Übersetzung
    if data == 'help':
        # ID aus context holen
        cid = context.user_data.get('selected_chat_id') or context.bot_data.get('selected_chat_id')
        lang = get_group_language(cid) or 'de'
        # HELP_TEXT verwenden statt Datei
        text = HELP_TEXT
        translated = translate_hybrid(text, target_lang=lang)
        # Zwischenspeichern
        path = f'user_manual_{lang}.md'
        with open(path, 'w', encoding='utf-8') as f:
            f.write(translated)
        # Dokument versenden
        await query.message.reply_document(
            document=open(path, 'rb'),
            filename=f'Handbuch_{lang}.md'
        )
        return

    # 3) Submenüs: welcome, rules, farewell, rss, exceptions, captcha
    # Splitting data into cid, func, [action]
    parts = data.split('_',2)
    if len(parts) >= 2 and parts[1] in ('welcome','rules','farewell','rss','exceptions','captcha','language'):
        cid = int(parts[0])
        func = parts[1]
        sub = parts[2] if len(parts)>=3 else None
        lang = get_lang(cid)
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
            en, ctype, behavior = get_captcha_settings(cid)
            kb = [
                [InlineKeyboardButton(f"{'✅ ' if en else ''}{tr('Aktiviert', lang) if en else tr('Deaktiviert', lang)}", callback_data=f"{cid}_captcha_toggle")],
                [InlineKeyboardButton(f"{'✅ ' if ctype=='button' else ''}{tr('Button', lang)}", callback_data=f"{cid}_captcha_type_button"),
                 InlineKeyboardButton(f"{'✅ ' if ctype=='math' else ''}{tr('Rechenaufgabe', lang)}", callback_data=f"{cid}_captcha_type_math")],
                [InlineKeyboardButton(f"{'✅ ' if behavior=='kick' else ''}{tr('Kick', lang)}", callback_data=f"{cid}_captcha_behavior_kick"),
                 InlineKeyboardButton(f"{'✅ ' if behavior=='timeout' else ''}{tr('Timeout', lang)}", callback_data=f"{cid}_captcha_behavior_timeout")],
                [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
            ]
            return await query.edit_message_text(tr('🔐 Captcha-Einstellungen', lang), reply_markup=InlineKeyboardMarkup(kb))

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
            rec = get_map[func](cid)
            text = rec[1] if rec else f"Keine {func}-Nachricht gesetzt."
            return await query.edit_message_text(text, reply_markup=back)
        if action == 'delete' and func in del_map:
            del_map[func](cid)
            await query.answer(tr(f"✅ {func.capitalize()} gelöscht.", get_lang(cid)), show_alert=True)
            return await query.edit_message_text(tr(f"{func.capitalize()} entfernt.", get_lang(cid)), reply_markup=back)
        if action == 'edit' and func in set_map:
            context.user_data['last_edit'] = (cid, f"{func}_edit")
            return await query.edit_message_text(f"✏️ Sende nun das neue {func}:", reply_markup=back)

    # 5) RSS-Detail
    if data.endswith('_rss_setrss'):
        cid = int(data.split('_',1)[0])
        if not get_rss_topic(cid):
            await query.answer('❗ Kein RSS-Topic gesetzt.', show_alert=True)
            return await show_group_menu(query, cid)
        context.user_data.update(awaiting_rss_url=True, rss_group_id=cid)
        await query.answer()
        return await query.edit_message_text('➡ Bitte sende die RSS-URL:', reply_markup=ForceReply(selective=True))
    if data.endswith('_rss_list'):
        cid = int(data.split('_',1)[0])
        feeds = db_list_rss_feeds(cid)
        text = 'Keine RSS-Feeds.' if not feeds else 'Aktive Feeds:\n' + '\n'.join(feeds)
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Zurück', callback_data=f'group_{cid}')]]))
    if data.endswith('_rss_stop'):
        cid = int(data.split('_',1)[0])
        remove_rss_feed(cid)
        await query.answer('✅ RSS gestoppt', show_alert=True)
        return await show_group_menu(query, cid)

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
        return await show_group_menu(query, cid)

    # Fallback: Hauptmenü
    selected = context.user_data.get('selected_chat_id')
    if selected:
        return await show_group_menu(query, selected)

    
# /menu 

def register_menu(app):

    app.add_handler(CallbackQueryHandler(menu_callback))

