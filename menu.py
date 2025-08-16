from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Update
from telegram.ext import CallbackQueryHandler, filters, MessageHandler, ContextTypes
from telegram.error import BadRequest
import re
from database import (
    get_link_settings, set_link_settings, get_welcome, set_welcome, delete_welcome, set_spam_policy_topic, get_spam_policy_topic,
    get_rules, set_rules, delete_rules, get_captcha_settings, list_topic_router_rules, add_topic_router_rule, delete_topic_router_rule,
    set_captcha_settings, get_farewell, set_farewell, delete_farewell, toggle_topic_router_rule,
    get_rss_topic, list_rss_feeds as db_list_rss_feeds, remove_rss_feed, get_ai_settings, set_ai_settings,
    is_daily_stats_enabled, set_daily_stats, get_mood_question, get_mood_topic, list_faqs, upsert_faq, delete_faq,
    get_group_language, set_group_language, list_forum_topics, count_forum_topics, get_night_mode, set_night_mode
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
         InlineKeyboardButton(tr('🌙 Nachtmodus', lang), callback_data=f"{cid}_night")],
        [InlineKeyboardButton(tr('🧹 Spamfilter', lang), callback_data=f"{cid}_spam"),
         InlineKeyboardButton(tr('🧭 Topic-Router', lang), callback_data=f"{cid}_router")],
        [InlineKeyboardButton(tr('📰 RSS', lang), callback_data=f"{cid}_rss"),
         InlineKeyboardButton(tr('📊 Statistiken', lang), callback_data=f"{cid}_stats")],
        [InlineKeyboardButton(tr('📥 Export CSV', lang), callback_data=f"{cid}_stats_export"),
         InlineKeyboardButton(f"📊 Tagesreport {status}", callback_data=f"{cid}_toggle_stats")],
        [InlineKeyboardButton(tr('🧠 Mood', lang), callback_data=f"{cid}_mood"),
         InlineKeyboardButton(tr('🌐 Sprache', lang), callback_data=f"{cid}_language")],
        [InlineKeyboardButton(tr('❓ FAQ', lang), callback_data=f"{cid}_faq"),
         InlineKeyboardButton(tr('🤖 KI',  lang), callback_data=f"{cid}_ai")],
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
        ai_faq, ai_rss = get_ai_settings(cid)
        kb = [
            [InlineKeyboardButton(tr('Auflisten', lang), callback_data=f"{cid}_rss_list"),
             InlineKeyboardButton(tr('Feed hinzufügen', lang), callback_data=f"{cid}_rss_setrss")],
            [InlineKeyboardButton(tr('Stoppen', lang), callback_data=f"{cid}_rss_stop")],
            [InlineKeyboardButton(f"{'✅' if ai_rss else '☐'} KI-Zusammenfassung", callback_data=f"{cid}_rss_ai_toggle")],
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

    elif func == 'faq' and sub is None:
        faqs = list_faqs(cid) or []
        lines = [f"• <code>{t}</code>" for t,_ in faqs[:20]]
        ai_faq, _ = get_ai_settings(cid)
        text = "❓ <b>Mini-FAQ</b>\n\n" + ("\n".join(lines) if lines else "Noch keine Einträge.") + \
            f"\n\nKI-Fallback: {'✅ an' if ai_faq else '❌ aus'}"
        kb = [
            [InlineKeyboardButton("➕ Eintrag", callback_data=f"{cid}_faq_add"),
            InlineKeyboardButton("🗑 Eintrag löschen", callback_data=f"{cid}_faq_del")],
            [InlineKeyboardButton(f"{'✅' if ai_faq else '☐'} KI-Fallback", callback_data=f"{cid}_faq_ai_toggle")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
        ]
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif func == 'faq' and sub == 'ai_toggle':
        ai_faq, _ = get_ai_settings(cid)
        set_ai_settings(cid, faq=not ai_faq)
        await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
        return await menu_callback(update, context)

    elif func == 'faq' and sub == 'add':
        context.user_data.update(awaiting_faq_add=True, faq_group_id=cid)
        return await query.message.reply_text(
            "Format:\n<Trigger> ⟶ <Antwort>",
            reply_markup=ForceReply(selective=True)
        )

    elif func == 'faq' and sub == 'del':
        context.user_data.update(awaiting_faq_del=True, faq_group_id=cid)
        return await query.message.reply_text(
            "Bitte sende den <Trigger>, der gelöscht werden soll.",
            reply_markup=ForceReply(selective=True)
        )
    
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

    elif func == 'router' and sub is None:
        rules = list_topic_router_rules(cid) or []
        lines = [f"#{rid} → topic {tgt} | {'ON' if en else 'OFF'} | del={do} warn={wn} | kw={kws or []} dom={doms or []}"
                for (rid,tgt,en,do,wn,kws,doms) in rules]
        text = "🧭 <b>Topic-Router</b>\n\n" + ("\n".join(lines) if lines else "Keine Regeln.")
        kb = [
            [InlineKeyboardButton("Regeln auffrischen", callback_data=f"{cid}_router")],
            [InlineKeyboardButton("➕ Keywords-Regel (Topic wählen)", callback_data=f"{cid}_router_tsel_kw"),
            InlineKeyboardButton("➕ Domains-Regel (Topic wählen)",  callback_data=f"{cid}_router_tsel_dom")],
            [InlineKeyboardButton("🗑 Regel löschen",   callback_data=f"{cid}_router_del"),
            InlineKeyboardButton("🔁 Regel togglen",  callback_data=f"{cid}_router_toggle")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
        ]
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif func == 'router' and sub:
        if sub == 'add_kw':
            context.user_data.update(awaiting_router_add_keywords=True, router_group_id=cid)
            return await query.message.reply_text("Format: <topic_id>; wort1, wort2, ...",
                                                reply_markup=ForceReply(selective=True))
        if sub == 'add_dom':
            context.user_data.update(awaiting_router_add_domains=True, router_group_id=cid)
            return await query.message.reply_text("Format: <topic_id>; domain1.tld, domain2.tld",
                                                reply_markup=ForceReply(selective=True))
        if sub == 'del':
            context.user_data.update(awaiting_router_delete=True, router_group_id=cid)
            return await query.message.reply_text("Gib die Regel-ID an, die gelöscht werden soll:",
                                                reply_markup=ForceReply(selective=True))
        if sub == 'toggle':
            context.user_data.update(awaiting_router_toggle=True, router_group_id=cid)
            return await query.message.reply_text("Format: <regel_id> on|off",
                                                reply_markup=ForceReply(selective=True))
        return await menu_callback(update, context)

    elif func == 'spam' and sub is None:
        pol = get_spam_policy_topic(cid, 0) or {}
        level = pol.get('level','off')
        emsg = pol.get('emoji_max_per_msg', 0) or 0
        rate = pol.get('max_msgs_per_10s', 0) or 0
        wl = ", ".join(pol.get('link_whitelist') or []) or "–"
        bl = ", ".join(pol.get('domain_blacklist') or []) or "–"
        text = (
            "🧹 <b>Spamfilter (Default / Topic 0)</b>\n\n"
            f"Level: <b>{level}</b>\n"
            f"Emoji/Msg: <b>{emsg}</b> • Flood/10s: <b>{rate}</b>\n"
            f"Whitelist: {wl}\nBlacklist: {bl}\n\n"
            "ℹ️ <i>Topic-spezifische Regeln setzt du am besten direkt im jeweiligen Topic mit</i> "
            "<code>/spamlevel …</code>."
        )
        kb = [
            [InlineKeyboardButton("Level ⏭", callback_data=f"{cid}_spam_lvl_cycle")],
            [InlineKeyboardButton("Emoji −", callback_data=f"{cid}_spam_emj_minus"),
            InlineKeyboardButton("Emoji +", callback_data=f"{cid}_spam_emj_plus")],
            [InlineKeyboardButton("Flood −", callback_data=f"{cid}_spam_rate_minus"),
            InlineKeyboardButton("Flood +", callback_data=f"{cid}_spam_rate_plus")],
            [InlineKeyboardButton("Whitelist bearbeiten", callback_data=f"{cid}_spam_wl_edit"),
            InlineKeyboardButton("Blacklist bearbeiten", callback_data=f"{cid}_spam_bl_edit")],
            [InlineKeyboardButton("Topic auswählen", callback_data=f"{cid}_spam_tsel")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
        ]
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif func == 'spam' and sub == 'tsel':
        return await query.edit_message_text(
            "🧹 <b>Spamfilter: Topic wählen</b>",
            reply_markup=_topics_keyboard(cid, page=0, purpose="spam"),
            parse_mode="HTML"
        )

    elif func == 'spam' and sub and sub.startswith('tp_'):
        # Paginierung: sub = 'tp_<page>'
        page = int(sub.split('_',1)[1])
        return await query.edit_message_reply_markup(reply_markup=_topics_keyboard(cid, page, purpose="spam"))

    elif func == 'spam' and sub and sub.startswith('t_'):
        # Auswahl: sub = 't_<topicid>'
        topic_id = int(sub.split('_',1)[1])
        pol = get_spam_policy_topic(cid, topic_id) or {}
        level = pol.get('level','off'); emsg = pol.get('emoji_max_per_msg',0) or 0; rate = pol.get('max_msgs_per_10s',0) or 0
        wl = ", ".join(pol.get('link_whitelist') or []) or "–"
        bl = ", ".join(pol.get('domain_blacklist') or []) or "–"

        text = (f"🧹 <b>Spamfilter – Topic {topic_id}</b>\n\n"
                f"Level: <b>{level}</b>\n"
                f"Emoji/Msg: <b>{emsg}</b> • Flood/10s: <b>{rate}</b>\n"
                f"Whitelist: {wl}\nBlacklist: {bl}")
        kb = [
            [InlineKeyboardButton("Level ⏭", callback_data=f"{cid}_spam_setlvl_{topic_id}")],
            [InlineKeyboardButton("Emoji −", callback_data=f"{cid}_spam_emj_-_{topic_id}"),
            InlineKeyboardButton("Emoji +", callback_data=f"{cid}_spam_emj_+_{topic_id}")],
            [InlineKeyboardButton("Flood −", callback_data=f"{cid}_spam_rate_-_{topic_id}"),
            InlineKeyboardButton("Flood +", callback_data=f"{cid}_spam_rate_+_{topic_id}")],
            [InlineKeyboardButton("Whitelist bearbeiten", callback_data=f"{cid}_spam_wl_edit_{topic_id}"),
            InlineKeyboardButton("Blacklist bearbeiten", callback_data=f"{cid}_spam_bl_edit_{topic_id}")],
            [InlineKeyboardButton("↩️ Zurück (Topics)", callback_data=f"{cid}_spam_tsel")]
        ]
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

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
                # Debug logging to see what we're getting from database
                logger.debug(f"Retrieved {func} record: {rec}")
                
                if rec:
                    text = rec[1] if len(rec) > 1 else "No text content"
                    media = rec[2] if len(rec) > 2 else None
                    
                    if media:
                        logger.debug(f"Media found: {media} (type: {type(media)})")
                        try:
                            # Try to send as photo regardless of prefix - let Telegram API validate
                            await query.message.reply_photo(
                                photo=media,
                                caption=text,
                                reply_markup=back,
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger.error(f"Error sending photo: {e}")
                            # Fallback - try as document if photo fails
                            try:
                                await query.message.reply_document(
                                    document=media,
                                    caption=text,
                                    reply_markup=back,
                                    parse_mode="HTML"
                                )
                            except Exception as e2:
                                logger.error(f"Error sending document: {e2}")
                                await query.edit_message_text(
                                    f"{text}\n\n⚠️ Bild konnte nicht geladen werden: {e}",
                                    reply_markup=back,
                                    parse_mode="HTML"
                                )
                    else:
                        await query.edit_message_text(text, reply_markup=back, parse_mode="HTML")
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
            elif sub == 'ai_toggle':
                ai_faq, ai_rss = get_ai_settings(cid)
                set_ai_settings(cid, rss=not ai_rss)
                await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
                return await menu_callback(update, context)

        elif func == 'spam' and sub and sub.startswith('setlvl_'):
            topic_id = int(sub.split('_',1)[1])
            pol = get_spam_policy_topic(cid, topic_id) or {'level':'off'}
            order = ['off','light','medium','strict']
            nxt = order[(order.index(pol.get('level','off'))+1) % len(order)]
            set_spam_policy_topic(cid, topic_id, level=nxt)
            await query.answer(f"Level: {nxt}", show_alert=True)
            # Re-render Topic-Detail
            update.callback_query.data = f"{cid}_spam_t_{topic_id}"
            return await menu_callback(update, context)

        elif func == 'spam' and sub and (sub.startswith('emj_') or sub.startswith('rate_') or sub.startswith('wl_edit_') or sub.startswith('bl_edit_')):
            parts = sub.split('_')
            if parts[0] == 'emj':
                op, topic_id = parts[1], int(parts[2])
                pol = get_spam_policy_topic(cid, topic_id) or {}
                cur = (pol.get('emoji_max_per_msg') or 0) + (1 if op == '+' else -1)
                set_spam_policy_topic(cid, topic_id, emoji_max_per_msg=max(0, cur))
                update.callback_query.data = f"{cid}_spam_t_{topic_id}"
                return await menu_callback(update, context)
            if parts[0] == 'rate':
                op, topic_id = parts[1], int(parts[2])
                pol = get_spam_policy_topic(cid, topic_id) or {}
                cur = (pol.get('max_msgs_per_10s') or 0) + (1 if op == '+' else -1)
                set_spam_policy_topic(cid, topic_id, max_msgs_per_10s=max(0, cur))
                update.callback_query.data = f"{cid}_spam_t_{topic_id}"
                return await menu_callback(update, context)
            if parts[0] in ('wl','bl') and parts[1]=='edit':
                topic_id = int(parts[2])
                if parts[0] == 'wl':
                    context.user_data.update(awaiting_spam_whitelist=True, spam_group_id=cid, spam_topic_id=topic_id)
                    return await query.message.reply_text("Sende Whitelist-Domains, Komma-getrennt:", reply_markup=ForceReply(selective=True))
                else:
                    context.user_data.update(awaiting_spam_blacklist=True, spam_group_id=cid, spam_topic_id=topic_id)
                    return await query.message.reply_text("Sende Blacklist-Domains, Komma-getrennt:", reply_markup=ForceReply(selective=True))

        elif func == 'router' and sub in ('tsel_kw','tsel_dom'):
            purpose = 'router_kw' if sub.endswith('kw') else 'router_dom'
            return await query.edit_message_text(
                "🧭 <b>Router: Ziel-Topic wählen</b>",
                reply_markup=_topics_keyboard(cid, page=0, purpose=purpose),
                parse_mode="HTML"
            )

        elif func in ('router_kw','router_dom') and sub and sub.startswith('tp_'):
            # Paginierung: func ist 'router_kw' oder 'router_dom'
            page = int(sub.split('_',1)[1])
            return await query.edit_message_reply_markup(
                reply_markup=_topics_keyboard(cid, page=page, purpose=func)
            )

        elif func == 'router' and sub and (sub.startswith('pick_kw_') or sub.startswith('pick_dom_')):
            topic_id = int(sub.split('_')[-1])
            if 'pick_kw_' in sub:
                context.user_data.update(awaiting_router_add_keywords=True, router_group_id=cid, router_target_tid=topic_id)
                return await query.message.reply_text("Sende Keywords (Komma-getrennt) für die Regel:", reply_markup=ForceReply(selective=True))
            else:
                context.user_data.update(awaiting_router_add_domains=True, router_group_id=cid, router_target_tid=topic_id)
                return await query.message.reply_text("Sende Domains (Komma-getrennt) für die Regel:", reply_markup=ForceReply(selective=True))
                
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

async def menu_free_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    # KORREKTUR 1: Text aus Caption und Text-Nachricht holen
    text = (msg.text or msg.caption or "").strip()
    photo_id = msg.photo[-1].file_id if msg.photo else None
    doc_id   = msg.document.file_id if msg.document else None
    media_id = photo_id or doc_id

    # KORREKTUR 2: 'last_edit' nur einmal aus context.user_data entfernen
    last_edit_data = context.user_data.pop('last_edit', None)
    if last_edit_data:
        cid, what = last_edit_data
        if what == 'welcome_edit':
            # KORREKTUR 3: Argument-Reihenfolge (chat_id, media_id, text)
            set_welcome(cid, media_id, text)
            return await msg.reply_text("✅ Begrüßung gespeichert.")
        elif what == 'rules_edit':
            set_rules(cid, media_id, text)
            return await msg.reply_text("✅ Regeln gespeichert.")
        elif what == 'farewell_edit':
            set_farewell(cid, media_id, text)
            return await msg.reply_text("✅ Abschied gespeichert.")

    # Warntext Linksperre speichern (nur Text)
    if context.user_data.pop('awaiting_link_warn', False):
        cid = context.user_data.pop('link_warn_group')
        set_link_settings(cid, warning_text=text)
        return await msg.reply_text("✅ Warn-Text gespeichert.")

    # 2) Spam-Whitelist (jetzt auch Topic-spezifisch)
    if context.user_data.pop('awaiting_spam_whitelist', False):
        cid = context.user_data.pop('spam_group_id')
        tid = context.user_data.pop('spam_topic_id', 0)
        wl = [d.strip().lower() for d in text.split(",") if d.strip()]
        set_spam_policy_topic(cid, tid, link_whitelist=wl)
        return await msg.reply_text(f"✅ Whitelist gespeichert (Topic {tid}).")

    # 3) Spam-Blacklist (Topic-spezifisch)
    if context.user_data.pop('awaiting_spam_blacklist', False):
        cid = context.user_data.pop('spam_group_id')
        tid = context.user_data.pop('spam_topic_id', 0)
        bl = [d.strip().lower() for d in text.split(",") if d.strip()]
        set_spam_policy_topic(cid, tid, domain_blacklist=bl)
        return await msg.reply_text(f"✅ Blacklist gespeichert (Topic {tid}).")

    # 4) Router: add keywords (mit Ziel-Topic)
    if context.user_data.pop('awaiting_router_add_keywords', False):
        cid = context.user_data.pop('router_group_id')
        tid = context.user_data.pop('router_target_tid', None)
        kws = [w.strip() for w in text.split(",") if w.strip()]
        if not tid or not kws:
            return await msg.reply_text("Bitte Keywords angeben.")
        rid = add_topic_router_rule(cid, tid, keywords=kws)
        return await msg.reply_text(f"✅ Regel #{rid} → Topic {tid} (Keywords) angelegt.")

    # 5) Router: add domains (mit Ziel-Topic)
    if context.user_data.pop('awaiting_router_add_domains', False):
        cid = context.user_data.pop('router_group_id')
        tid = context.user_data.pop('router_target_tid', None)
        doms = [d.strip().lower() for d in text.split(",") if d.strip()]
        if not tid or not doms:
            return await msg.reply_text("Bitte Domains angeben.")
        rid = add_topic_router_rule(cid, tid, domains=doms)
        return await msg.reply_text(f"✅ Regel #{rid} → Topic {tid} (Domains) angelegt.")
    # 6) Router: delete
    if context.user_data.pop('awaiting_router_delete', False):
        cid = context.user_data.pop('router_group_id')
        if not text.isdigit():
            return await msg.reply_text("Bitte eine numerische Regel-ID senden.")
        delete_topic_router_rule(cid, int(text))
        return await msg.reply_text("🗑 Regel gelöscht.")
    # 7) Router: toggle
    if context.user_data.pop('awaiting_router_toggle', False):
        cid = context.user_data.pop('router_group_id')
        m = re.match(r'^\s*(\d+)\s+(on|off)\s*$', text, re.I)
        if not m:
            return await msg.reply_text("Format: <regel_id> on|off")
        rid = int(m.group(1)); on = m.group(2).lower() == "on"
        toggle_topic_router_rule(cid, rid, on)
        return await msg.reply_text("🔁 Regel umgeschaltet.")
    # 8) FAQ add
    if context.user_data.pop('awaiting_faq_add', False):
        cid = context.user_data.pop('faq_group_id')
        if "⟶" not in text and "->" not in text:
            return await msg.reply_text("Bitte im Format <Trigger> ⟶ <Antwort> senden.")
        splitter = "⟶" if "⟶" in text else "->"
        trig, ans = [p.strip() for p in text.split(splitter, 1)]
        upsert_faq(cid, trig, ans)
        return await msg.reply_text("✅ FAQ gespeichert.")

    # 9) FAQ delete
    if context.user_data.pop('awaiting_faq_del', False):
        cid = context.user_data.pop('faq_group_id')
        delete_faq(cid, text.strip())
        return await msg.reply_text("🗑 FAQ gelöscht (falls vorhanden).")
    
TOPICS_PAGE_SIZE = 10

def _topics_keyboard(cid:int, page:int, purpose:str):
    # purpose: 'spam' | 'router_kw' | 'router_dom'
    offset = page * TOPICS_PAGE_SIZE
    rows = list_forum_topics(cid, limit=TOPICS_PAGE_SIZE, offset=offset)
    total = count_forum_topics(cid)
    kb = []
    for topic_id, name, _ in rows:
        if purpose == "spam":
            cb = f"{cid}_spam_t_{topic_id}"
        elif purpose == "router_kw":
            cb = f"{cid}_router_pick_kw_{topic_id}"
        else:
            cb = f"{cid}_router_pick_dom_{topic_id}"
        kb.append([InlineKeyboardButton(name[:56], callback_data=cb)])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"{cid}_{purpose}_tp_{page-1}"))
    if offset + TOPICS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"{cid}_{purpose}_tp_{page+1}"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton("↩️ Zurück", callback_data=f"group_{cid}")])
    return InlineKeyboardMarkup(kb)

# /menu 

def register_menu(app):

    app.add_handler(CallbackQueryHandler(menu_callback))
    # KORREKTUR: filters.document statt filters.Document (lowercase!)
    app.add_handler(MessageHandler(
        filters.REPLY & (filters.TEXT | filters.PHOTO | filters.document) & filters.ChatType.GROUPS,
        menu_free_text_handler
    ), group=1)