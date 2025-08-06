from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Update
from telegram.ext import CallbackQueryHandler, filters, MessageHandler, ContextTypes
from telegram.error import BadRequest
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

def build_group_menu(cid):
    # TODO: Hier deine InlineKeyboardMarkup-Erstellung einbauen
    lang = get_group_language(cid) or 'de'
    status = tr('Aktiv', lang) if is_daily_stats_enabled(cid) else tr('Inaktiv', lang)
    buttons = [
        [InlineKeyboardButton(tr('Begrüßung', lang), callback_data=f"{cid}_welcome"),
         InlineKeyboardButton(tr('🔐 Captcha', lang), callback_data=f"{cid}_captcha")],
        [InlineKeyboardButton(tr('Regeln', lang), callback_data=f"{cid}_rules"),
         InlineKeyboardButton(tr('Abschied', lang), callback_data=f"{cid}_farewell")],
        [InlineKeyboardButton(tr('🔗 Linksperre', lang), callback_data=f"{cid}_linkprot"),
         InlineKeyboardButton(tr('📰 RSS', lang), callback_data=f"{cid}_rss")],
        [InlineKeyboardButton(tr('🗑 Bereinigen', lang), callback_data=f"{cid}_clean_delete"),
         InlineKeyboardButton(tr('📊 Statistiken', lang), callback_data=f"{cid}_stats")],
        [InlineKeyboardButton(tr('📥 Export CSV', lang), callback_data=f"{cid}_stats_export"),
         InlineKeyboardButton(f"📊 Tagesreport {status}", callback_data=f"{cid}_toggle_stats")],
        [InlineKeyboardButton(tr('✍️ Mood-Frage ändern', lang), callback_data=f"{cid}_edit_mood_q"),
         InlineKeyboardButton(tr('🌐 Sprache', lang), callback_data=f"{cid}_language")],
        [InlineKeyboardButton(tr('📖 Handbuch', lang), callback_data="help"),
         InlineKeyboardButton(tr('🔄 Gruppe wechseln', lang), callback_data="group_select")]
    ]
    return InlineKeyboardMarkup(buttons)

async def show_group_menu(query=None, cid=None, context=None):
    title = "📋 Gruppenmenü"
    markup = build_group_menu(cid)

    if query:
        try:
            if (query.message.text != title) or (query.message.reply_markup != markup):
                await query.edit_message_text(title, reply_markup=markup)
            else:
                await query.edit_message_text(title + "\u200b", reply_markup=markup)
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                raise
    else:
        await context.bot.send_message(chat_id=cid, text=title, reply_markup=markup)

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # DEBUG: Zeige was geklickt wurde
    print(f"DEBUG: Callback data = {data}")

    # 1) Gruppe wurde ausgewählt
    if data.startswith("group_"):
        print(f"DEBUG: Group selected")
        cid = int(data.split("_")[1])
        context.user_data["selected_chat_id"] = cid
        return await show_group_menu(query=query, cid=cid, context=context)

    # 2) Spezielle Handler (help, group_select)
    if data == 'help':
        print(f"DEBUG: Help clicked")
        cid = context.user_data.get('selected_chat_id')
        lang = get_group_language(cid) or 'de'
        translated = translate_hybrid(HELP_TEXT, target_lang=lang)
        path = f'user_manual_{lang}.md'
        with open(path, 'w', encoding='utf-8') as f:
            f.write(translated)
        await query.message.reply_document(document=open(path, 'rb'), filename=f'Handbuch_{lang}.md')
        return await show_group_menu(query=query, cid=cid, context=context)

    if data == 'group_select':
        print(f"DEBUG: Group select clicked")
        groups = get_visible_groups(update.effective_user.id)
        if not groups:
            return await query.edit_message_text("⚠️ Keine Gruppen verfügbar.")
        kb = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid, title in groups]
        return await query.edit_message_text("Wähle eine Gruppe:", reply_markup=InlineKeyboardMarkup(kb))

    # 3) Parse Callback-Daten
    parts = data.split("_", 2)
    print(f"DEBUG: parts = {parts}")
    
    if not parts[0].isdigit():
        print(f"DEBUG: First part not digit, fallback to main menu")
        cid = context.user_data.get("selected_chat_id")
        if not cid:
            return await query.edit_message_text("⚠️ Keine Gruppe ausgewählt.")
        return await show_group_menu(query=query, cid=cid, context=context)

    cid = int(parts[0])
    func = parts[1] if len(parts) > 1 else None
    sub = parts[2] if len(parts) >= 3 else None
    
    print(f"DEBUG: cid={cid}, func={func}, sub={sub}")
    
    # 4) SUBMENÜS ZUERST
    if func in ('welcome', 'rules', 'farewell') and sub is None:
        print(f"DEBUG: Welcome/Rules/Farewell submenu for {func}")
        kb = [
            [InlineKeyboardButton(tr('Bearbeiten', lang), callback_data=f"{cid}_{func}_edit"),
             InlineKeyboardButton(tr('Anzeigen', lang), callback_data=f"{cid}_{func}_show")],
            [InlineKeyboardButton(tr('Löschen', lang), callback_data=f"{cid}_{func}_delete")],
            [InlineKeyboardButton(tr('⬅ Hauptmenü', lang), callback_data=f"group_{cid}")]
        ]
        text = tr(f"⚙️ {func.capitalize()} verwalten:", lang)
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif func == 'rss' and sub is None:
        print(f"DEBUG: RSS submenu")
        kb = [
            [InlineKeyboardButton(tr('Auflisten', lang), callback_data=f"{cid}_rss_list"),
             InlineKeyboardButton(tr('Feed hinzufügen', lang), callback_data=f"{cid}_rss_setrss")],
            [InlineKeyboardButton(tr('Stoppen', lang), callback_data=f"{cid}_rss_stop")],
            [InlineKeyboardButton(tr('⬅ Hauptmenü', lang), callback_data=f"group_{cid}")]
        ]
        text = tr('📰 RSS verwalten', lang)
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # Captcha Submenü  
    elif func == 'captcha' and sub is None:
        en, ctype, behavior = get_captcha_settings(cid)
        kb = [
            [InlineKeyboardButton(f"{'✅ ' if en else ''}{tr('Aktiviert', lang) if en else tr('Deaktiviert', lang)}", 
                                 callback_data=f"{cid}_captcha_toggle")],
            [InlineKeyboardButton(f"{'✅' if ctype=='button' else '☐'} {tr('Button', lang)}", 
                                 callback_data=f"{cid}_captcha_type_button"),
             InlineKeyboardButton(f"{'✅' if ctype=='math' else '☐'} {tr('Rechenaufgabe', lang)}", 
                                 callback_data=f"{cid}_captcha_type_math")],
            [InlineKeyboardButton(f"{'✅' if behavior=='kick' else '☐'} {tr('Kick', lang)}", 
                                 callback_data=f"{cid}_captcha_behavior_kick"),
             InlineKeyboardButton(f"{'✅' if behavior=='timeout' else '☐'} {tr('Timeout', lang)}", 
                                 callback_data=f"{cid}_captcha_behavior_timeout")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
        ]
        return await query.edit_message_text(tr('🔐 Captcha-Einstellungen', lang), reply_markup=InlineKeyboardMarkup(kb))

    # Language Submenü
    elif func == 'language' and sub is None:
        cur = get_lang(cid) or 'de'
        kb = [[InlineKeyboardButton(f"{'✅ ' if c==cur else ''}{n}", callback_data=f"{cid}_setlang_{c}")] 
              for c,n in LANGUAGES.items()]
        kb.append([InlineKeyboardButton('↩️ Zurück', callback_data=f'group_{cid}')])
        return await query.edit_message_text(tr('🌐 Wähle Sprache:', cur), reply_markup=InlineKeyboardMarkup(kb))

    # Linksperre Submenü
    elif func == 'linkprot' and sub is None:
        prot_on, warn_on, warn_text, except_on = get_link_settings(cid)
        kb = [
            [InlineKeyboardButton(f"{'✅' if prot_on else '☐'} {tr('Linkschutz aktiv', lang)}",
                                  callback_data=f"{cid}_linkprot_toggle")],
            [InlineKeyboardButton(f"{'✅' if warn_on else '☐'} {tr('Warn-Text senden', lang)}",
                                  callback_data=f"{cid}_linkprot_warn_toggle")],
            [InlineKeyboardButton(tr('Warn-Text bearbeiten', lang), callback_data=f"{cid}_linkprot_edit")],
            [InlineKeyboardButton(f"{'✅' if except_on else '☐'} {tr('Ausnahmen (Settopic)', lang)}",
                                  callback_data=f"{cid}_linkprot_exc_toggle")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")],
        ]
        return await query.edit_message_text(tr('🔧 Linksperre-Einstellungen:', lang), reply_markup=InlineKeyboardMarkup(kb))

    # 5) DANACH erst die Sub-Aktionen...
    if func and sub:
        
        # Welcome/Rules/Farewell Aktionen
        if func in ('welcome', 'rules', 'farewell'):
            get_map = {'welcome': get_welcome, 'rules': get_rules, 'farewell': get_farewell}
            set_map = {'welcome': set_welcome, 'rules': set_rules, 'farewell': set_farewell}
            del_map = {'welcome': delete_welcome, 'rules': delete_rules, 'farewell': delete_farewell}
            
            if sub == 'show' and func in get_map:
                rec = get_map[func](cid)
                text = rec[1] if rec else f"Keine {func}-Nachricht gesetzt."
                return await query.edit_message_text(text, reply_markup=back)
            elif sub == 'delete' and func in del_map:
                del_map[func](cid)
                await query.answer(tr(f"✅ {func.capitalize()} gelöscht.", lang), show_alert=True)
                return await query.edit_message_text(tr(f"{func.capitalize()} entfernt.", lang), reply_markup=back)
            elif sub == 'edit' and func in set_map:
                context.user_data['last_edit'] = (cid, f"{func}_edit")
                return await query.edit_message_text(f"✏️ Sende nun das neue {func}:", reply_markup=back)

        # Captcha Aktionen
        elif func == 'captcha':
            en, ctype, behavior = get_captcha_settings(cid)
            if sub == 'toggle':
                set_captcha_settings(cid, not en, ctype, behavior)
                await query.answer(tr(f"Captcha {'aktiviert' if not en else 'deaktiviert'}", lang), show_alert=True)
            elif sub in ('type_button', 'type_math'):
                new_type = sub.split('_', 1)[1]
                set_captcha_settings(cid, en, new_type, behavior)
                await query.answer(tr("Captcha-Typ geändert", lang), show_alert=True)
            elif sub in ('behavior_kick', 'behavior_timeout'):
                new_behavior = sub.split('_', 1)[1]
                set_captcha_settings(cid, en, ctype, new_behavior)
                await query.answer(tr("Captcha-Verhalten geändert", lang), show_alert=True)
            return await show_group_menu(query=query, cid=cid, context=context)

        # RSS Aktionen
        elif func == 'rss':
            if sub == 'setrss':
                if not get_rss_topic(cid):
                    await query.answer('❗ Kein RSS-Topic gesetzt.', show_alert=True)
                    return await show_group_menu(query=query, cid=cid, context=context)
                context.user_data.update(awaiting_rss_url=True, rss_group_id=cid)
                return await query.edit_message_text('➡ Bitte sende die RSS-URL:', reply_markup=ForceReply(selective=True))
            elif sub == 'list':
                feeds = db_list_rss_feeds(cid)
                text = 'Keine RSS-Feeds.' if not feeds else 'Aktive Feeds:\n' + '\n'.join(feeds)
                return await query.edit_message_text(text, reply_markup=back)
            elif sub == 'stop':
                remove_rss_feed(cid)
                await query.answer('✅ RSS gestoppt', show_alert=True)
                return await show_group_menu(query=query, cid=cid, context=context)

        # Linksperre Aktionen
        elif func == 'linkprot':
            prot_on, warn_on, warn_text, except_on = get_link_settings(cid)
            if sub == "toggle":
                set_link_settings(cid, protection=not prot_on)
                await query.answer(tr(f"Linkschutz {'aktiviert' if not prot_on else 'deaktiviert'}", lang), show_alert=True)
            elif sub == "warn_toggle":
                set_link_settings(cid, warning_on=not warn_on)
                await query.answer(tr(f"Warn-Text {'aktiviert' if not warn_on else 'deaktiviert'}", lang), show_alert=True)
            elif sub == "exc_toggle":
                set_link_settings(cid, exceptions_on=not except_on)
                await query.answer(tr(f"Ausnahmen {'aktiviert' if not except_on else 'deaktiviert'}", lang), show_alert=True)
            elif sub == "edit":
                context.user_data['awaiting_link_warn'] = True
                context.user_data['link_warn_group'] = cid
                return await query.message.reply_text(tr("Sende jetzt deinen neuen Warn-Text:", lang),
                                                  reply_markup=ForceReply(selective=True))
            return await show_group_menu(query=query, cid=cid, context=context)

        # Language setzen
        elif sub.startswith('setlang_'):
            lang_code = sub.split('_', 1)[1]
            set_group_language(cid, lang_code)
            await query.answer(tr(f"Gruppensprache gesetzt: {LANGUAGES[lang_code]}", lang_code), show_alert=True)
            return await show_group_menu(query=query, cid=cid, context=context)

    # 6) DANACH die Einzelfunktionen...
    if func == 'toggle_stats':
        cur = is_daily_stats_enabled(cid)
        set_daily_stats(cid, not cur)
        await query.answer(tr(f"Tagesstatistik {'aktiviert' if not cur else 'deaktiviert'}", lang), show_alert=True)
        return await show_group_menu(query=query, cid=cid, context=context)

    elif func == 'clean_delete':
        await query.answer('⏳ Bereinige…')
        removed = await clean_delete_accounts_for_chat(cid, context.bot)
        text = f"✅ {removed} Accounts entfernt."
        return await query.edit_message_text(text, reply_markup=back)

    elif func == 'stats_export':
        return await export_stats_csv_command(update, context)

    elif func == 'stats':
        context.user_data['stats_group_id'] = cid
        return await stats_command(update, context)

    elif func == "edit_mood_q":
        context.user_data['awaiting_mood_question'] = True
        context.user_data['mood_group_id'] = cid
        return await query.message.reply_text(tr('Bitte sende deine neue Mood-Frage:', lang),
                                              reply_markup=ForceReply(selective=True))

    # Fallback: Hauptmenü
    cid = context.user_data.get('selected_chat_id')
    if cid:
        return await show_group_menu(query=query, cid=cid, context=context)

# /menu 

def register_menu(app):

    app.add_handler(CallbackQueryHandler(menu_callback))
