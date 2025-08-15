from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Update
from telegram.ext import CallbackQueryHandler, filters, MessageHandler, ContextTypes
from telegram.error import BadRequest
from database import (
    get_link_settings, set_link_settings, get_welcome, set_welcome, delete_welcome,
    get_rules, set_rules, delete_rules, get_captcha_settings,
    set_captcha_settings, get_farewell, set_farewell, delete_farewell,
    get_rss_topic, list_rss_feeds as db_list_rss_feeds, remove_rss_feed,
    is_daily_stats_enabled, set_daily_stats, get_mood_question, get_mood_topic,
    get_group_language, set_group_language,
    get_night_mode, set_night_mode
)
from zoneinfo import ZoneInfo
from access import get_visible_groups
from statistic import stats_command, export_stats_csv_command
from utils import clean_delete_accounts_for_chat, tr
from translator import translate_hybrid
from patchnotes import PATCH_NOTES, __version__
from user_manual import HELP_TEXT
import logging
import datetime

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
         InlineKeyboardButton(tr('🌙 Nachtmodus', lang), callback_data=f"{cid}_night")],  # <-- NEU
        [InlineKeyboardButton(tr('📰 RSS', lang), callback_data=f"{cid}_rss"),
         InlineKeyboardButton(tr('📊 Statistiken', lang), callback_data=f"{cid}_stats")],
        [InlineKeyboardButton(tr('📥 Export CSV', lang), callback_data=f"{cid}_stats_export"),
         InlineKeyboardButton(f"📊 Tagesreport {status}", callback_data=f"{cid}_toggle_stats")],
        [InlineKeyboardButton(tr('🧠 Mood', lang), callback_data=f"{cid}_mood"),
         InlineKeyboardButton(tr('🌐 Sprache', lang), callback_data=f"{cid}_language")],
        [InlineKeyboardButton(tr('📖 Handbuch', lang), callback_data="help"),
         InlineKeyboardButton(tr('📝 Patchnotes', lang), callback_data="patchnotes")],
        [InlineKeyboardButton(tr('🔄 Gruppe wechseln', lang), callback_data="group_select")]
    ]
    return InlineKeyboardMarkup(buttons)

async def show_group_menu(query=None, cid=None, context=None):
    title = tr("📋 Gruppenmenü", get_group_language(cid) or 'de')
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

    # Patchnotes-Handler direkt nach data = query.data
    if data == 'patchnotes':
        lang = get_group_language(context.user_data.get('selected_chat_id')) or 'de'
        notes = PATCH_NOTES
        if lang != 'de':
            notes = translate_hybrid(PATCH_NOTES, target_lang=lang)
        text = f"📝 <b>Patchnotes v{__version__}</b>\n\n{notes}"
        await query.message.reply_text(text, parse_mode="HTML")
        cid = context.user_data.get('selected_chat_id')
        return await show_group_menu(query=query, cid=cid, context=context)

    # 1) Gruppe wurde ausgewählt (KORRIGIERT)
    if data.startswith("group_"):
        print(f"DEBUG: Group selected")
        parts = data.split("_", 1)
        # Korrigierte Prüfung für negative IDs
        is_valid_id = len(parts) == 2 and (parts[1].isdigit() or (parts[1].startswith('-') and parts[1][1:].isdigit()))
        if not is_valid_id:
            await query.answer("Ungültige Auswahl.", show_alert=True)
            return
        
        cid = int(parts[1])
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
    
    # KORREKTE PRÜFUNG FÜR POSITIVE UND NEGATIVE IDs
    if not (parts[0].startswith('-') and parts[0][1:].isdigit()) and not parts[0].isdigit():
        print(f"DEBUG: First part not a valid ID, fallback to main menu")
        cid = context.user_data.get("selected_chat_id")
        if not cid:
            return await query.edit_message_text("⚠️ Keine Gruppe ausgewählt.")
        return await show_group_menu(query=query, cid=cid, context=context)

    cid = int(parts[0])
    func = parts[1] if len(parts) > 1 else None
    sub = parts[2] if len(parts) > 2 else None
    lang = get_group_language(cid)
    back = InlineKeyboardMarkup([[InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]])

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

    elif func == 'night' and sub is None:
        en, s, e, del_non_admin, warn_once, tz, hard_mode, override_until = get_night_mode(cid)
        def mm_to_str(m): return f"{m//60:02d}:{m%60:02d}"
        ov_txt = override_until.strftime("%d.%m. %H:%M") if override_until else "–"
        text = (
            f"🌙 <b>{tr('Nachtmodus', lang)}</b>\n\n"
            f"{tr('Status', lang)}: {'✅ ' + tr('Aktiv', lang) if en else '❌ ' + tr('Inaktiv', lang)}\n"
            f"{tr('Start', lang)}: {mm_to_str(s)}  •  {tr('Ende', lang)}: {mm_to_str(e)}  •  TZ: {tz}\n"
            f"{tr('Harter Modus', lang)}: {'✅' if hard_mode else '❌'}\n"
            f"{tr('Nicht-Admin-Nachrichten löschen', lang)}: {'✅' if del_non_admin else '❌'}\n"
            f"{tr('Nur einmal pro Nacht warnen', lang)}: {'✅' if warn_once else '❌'}\n"
            f"{tr('Sofortige Ruhephase (Override) bis', lang)}: {ov_txt}"
        )
        kb = [
            [InlineKeyboardButton(f"{'✅' if en else '☐'} {tr('Aktivieren/Deaktivieren', lang)}",
                                callback_data=f"{cid}_night_toggle")],
            [InlineKeyboardButton(tr('Startzeit ändern', lang), callback_data=f"{cid}_night_set_start"),
            InlineKeyboardButton(tr('Endzeit ändern', lang), callback_data=f"{cid}_night_set_end")],
            [InlineKeyboardButton(f"{'✅' if hard_mode else '☐'} {tr('Harter Modus', lang)}",
                                callback_data=f"{cid}_night_hard_toggle")],
            [InlineKeyboardButton(f"{'✅' if del_non_admin else '☐'} {tr('Nicht-Admin löschen', lang)}",
                                callback_data=f"{cid}_night_del_toggle")],
            [InlineKeyboardButton(f"{'✅' if warn_once else '☐'} {tr('Einmal warnen', lang)}",
                                callback_data=f"{cid}_night_warnonce_toggle")],
            [InlineKeyboardButton(f"⚡ {tr('Sofort', lang)} 15m", callback_data=f"{cid}_night_quiet_15m"),
            InlineKeyboardButton(f"⚡ {tr('Sofort', lang)} 1h",  callback_data=f"{cid}_night_quiet_1h"),
            InlineKeyboardButton(f"⚡ {tr('Sofort', lang)} 8h",  callback_data=f"{cid}_night_quiet_8h")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
        ]
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif func == 'mood' and sub is None:
        q = get_mood_question(cid) or tr('Wie fühlst du dich heute?', get_group_language(cid) or 'de')
        topic_id = get_mood_topic(cid)
        topic_txt = str(topic_id) if topic_id else tr('Nicht gesetzt', lang)
        text = (
            f"🧠 <b>{tr('Mood-Einstellungen', lang)}</b>\n\n"
            f"• {tr('Aktuelle Frage', lang)}:\n{q}\n\n"
            f"• {tr('Topic-ID', lang)}: {topic_txt}"
        )
        kb = [
            [InlineKeyboardButton(tr('Frage anzeigen', lang), callback_data=f"{cid}_mood_show"),
            InlineKeyboardButton(tr('Frage ändern', lang), callback_data=f"{cid}_edit_mood_q")],
            [InlineKeyboardButton(tr('Jetzt senden (Topic)', lang), callback_data=f"{cid}_mood_send")],
            [InlineKeyboardButton(tr('Topic setzen (Hilfe)', lang), callback_data=f"{cid}_mood_topic_help")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
        ]
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    # Nachtmodus Aktionen (lokalisiert)
    elif func == 'night' and sub:
        en, s, e, del_non_admin, warn_once, tz, hard_mode, override_until = get_night_mode(cid)
        if sub == 'toggle':
            set_night_mode(cid, enabled=not en)
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
        elif sub == 'hard_toggle':
            set_night_mode(cid, hard_mode=not hard_mode)
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
        elif sub == 'del_toggle':
            set_night_mode(cid, delete_non_admin_msgs=not del_non_admin)
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
        elif sub == 'warnonce_toggle':
            set_night_mode(cid, warn_once=not warn_once)
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
        elif sub == 'set_start':
            context.user_data['awaiting_nm_time'] = ('start', cid)
            return await query.message.reply_text(tr('Bitte Startzeit im Format HH:MM senden (z. B. 22:00).', lang),
                                                reply_markup=ForceReply(selective=True))
        elif sub == 'set_end':
            context.user_data['awaiting_nm_time'] = ('end', cid)
            return await query.message.reply_text(tr('Bitte Endzeit im Format HH:MM senden (z. B. 06:00).', lang),
                                                reply_markup=ForceReply(selective=True))
        elif sub.startswith('quiet_'):
            dur_map = {'15m': 15, '1h': 60, '8h': 480}
            key = sub.split('_', 1)[1]
            minutes = dur_map.get(key)
            if minutes:
                now = datetime.datetime.now(ZoneInfo(tz))
                set_night_mode(cid, override_until=now + datetime.timedelta(minutes=minutes))
                await query.answer(tr('Ruhephase bis', lang) + f" {(now + datetime.timedelta(minutes=minutes)).strftime('%H:%M')}", show_alert=True)
        # Re-render Submenü
        return await menu_callback(update, context)

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
                # Annahme: rec = (id, text, media) oder (id, text) falls kein media
                if rec:
                    text = rec[1]
                    media = rec[2] if len(rec) > 2 else None
                    if media:
                        # Prüfe, ob es eine Telegram File-ID ist (beginnt meist mit "AgAC" oder "CQAC")):
                        if isinstance(media, str) and (media.startswith("AgAC") or media.startswith("CQAC")):
                            await query.message.reply_photo(photo=media, caption=text, reply_markup=back)
                        else:
                            # Andernfalls als Datei öffnen (lokaler Pfad)
                            try:
                                with open(media, "rb") as f:
                                    await query.message.reply_photo(photo=f, caption=text, reply_markup=back)
                            except Exception as e:
                                await query.edit_message_text(f"{text}\n\n⚠️ Bild konnte nicht geladen werden: {e}", reply_markup=back)
                    else:
                        await query.edit_message_text(text, reply_markup=back)
                else:
                    await query.edit_message_text(f"Keine {func}-Nachricht gesetzt.", reply_markup=back)
                return
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
                if not feeds:
                    text = 'Keine RSS-Feeds.'
                else:
                    # Konvertiere die Tupel in lesbare Strings
                    feed_strings = []
                    for feed in feeds:
                        if isinstance(feed, tuple):
                            # Prüfe ob es sich um ein (url, title) Tupel handelt
                            if len(feed) >= 2:
                                feed_strings.append(f"{feed[1]}: {feed[0]}")
                            else:
                                feed_strings.append(str(feed[0]))
                        else:
                            # Falls es bereits ein String ist
                            feed_strings.append(str(feed))
        
                    text = 'Aktive Feeds:\n' + '\n'.join(feed_strings)
    
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

        # === Sprachcode setzen ===
        elif func == 'setlang' and sub:
            lang_code = sub  # sub enthält z.B. 'de', 'en', ...
            set_group_language(cid, lang_code)
            # Bestätigung im gewählten Ziel-Langcode
            await query.answer(
                tr(f"Gruppensprache gesetzt: {LANGUAGES.get(lang_code, lang_code)}", lang_code),
                show_alert=True
            )

        # Mood Aktionen
        elif func == 'mood':
            if sub == 'show':
                q = get_mood_question(cid) or tr('Wie fühlst du dich heute?', get_group_language(cid) or 'de')
                return await query.edit_message_text(f"📖 {tr('Aktuelle Mood-Frage', lang)}:\n\n{q}",
                                                     reply_markup=InlineKeyboardMarkup(
                                                         [[InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"{cid}_mood")]]
                                                     ))
            elif sub == 'send':
                q = get_mood_question(cid) or tr('Wie fühlst du dich heute?', get_group_language(cid) or 'de')
                topic_id = get_mood_topic(cid)
                if not topic_id:
                    await query.answer(tr('❗ Kein Mood-Topic gesetzt. Sende /setmoodtopic im gewünschten Thema.', lang), show_alert=True)
                    return await menu_callback(update, context)
                # Inline-Buttons wie im Mood-Feature
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("👍", callback_data="mood_like"),
                    InlineKeyboardButton("👎", callback_data="mood_dislike"),
                    InlineKeyboardButton("🤔", callback_data="mood_think"),
                ]])
                await context.bot.send_message(chat_id=cid, text=q, reply_markup=kb, message_thread_id=topic_id)
                await query.answer(tr('✅ Mood-Frage gesendet.', lang), show_alert=True)
                return await menu_callback(update, context)
            elif sub == 'topic_help':
                help_txt = (
                    "🧵 <b>Topic setzen</b>\n\n"
                    "1) Öffne das gewünschte Forum-Thema.\n"
                    "2) Sende dort <code>/setmoodtopic</code>\n"
                    f"   {tr('(oder antworte in dem Thema auf eine Nachricht und sende den Befehl)', lang)}.\n"
                    "3) Fertig – zukünftige Mood-Fragen landen in diesem Thema."
                )
                return await query.edit_message_text(help_txt, parse_mode="HTML",
                                                     reply_markup=InlineKeyboardMarkup(
                                                        [[InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"{cid}_mood")]]
                                                     ))

    # 6) DANACH die Einzelfunktionen...
    if func == 'toggle' and sub == 'stats':
        cur = is_daily_stats_enabled(cid)
        set_daily_stats(cid, not cur)
        await query.answer(tr(f"Tagesstatistik {'aktiviert' if not cur else 'deaktiviert'}", lang), show_alert=True)
        return await show_group_menu(query=query, cid=cid, context=context)

    elif func == 'clean' and sub == 'delete':
        await query.answer('⏳ Bereinige…')
        try:
            # Debug-Ausgabe zur Fehleranalyse
            print(f"DEBUG: Starting clean_delete for chat_id={cid}")
            removed = await clean_delete_accounts_for_chat(cid, context.bot)
            text = f"✅ {removed} Accounts entfernt."
            return await query.edit_message_text(text, reply_markup=back)
        except Exception as e:
            # Fehler abfangen und loggen
            print(f"ERROR in clean_delete: {str(e)}")
            error_text = f"⚠️ Fehler bei der Bereinigung: {str(e)}"
            return await query.edit_message_text(error_text, reply_markup=back)

    elif func == 'stats' and sub == 'export':
        # Rufe den CSV-Export auf
        return await export_stats_csv_command(update, context)

    elif func == 'stats' and not sub:
        context.user_data['stats_group_id'] = cid
        return await stats_command(update, context)

    # Mood-Frage ändern (korrigierter Handler)
    elif func == 'edit' and sub == 'mood_q':
        print("DEBUG: Mood-Frage ändern erkannt!")
        context.user_data['awaiting_mood_question'] = True
        context.user_data['mood_group_id'] = cid
        return await query.message.reply_text(tr('Bitte sende deine neue Mood-Frage:', lang),
                                          reply_markup=ForceReply(selective=True))
                                          
    # Original-Handler beibehalten für Abwärtskompatibilität
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
