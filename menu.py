from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Update
from telegram.ext import CallbackQueryHandler, filters, MessageHandler, ContextTypes, CommandHandler
from telegram.error import BadRequest
import re
from database import (
    get_link_settings, set_link_settings, get_welcome, set_welcome, delete_welcome, set_spam_policy_topic, get_spam_policy_topic,
    get_rules, set_rules, delete_rules, get_captcha_settings, list_topic_router_rules, add_topic_router_rule, delete_topic_router_rule,
    set_captcha_settings, get_farewell, set_farewell, delete_farewell, toggle_topic_router_rule, set_mood_question,
    get_rss_topic, list_rss_feeds as db_list_rss_feeds, remove_rss_feed, get_ai_settings, set_ai_settings,
    is_daily_stats_enabled, set_daily_stats, get_mood_question, get_mood_topic, list_faqs, upsert_faq, delete_faq,
    get_group_language, set_group_language, list_forum_topics, count_forum_topics, get_night_mode, set_night_mode,
    set_pending_input, get_pending_inputs, get_pending_input, clear_pending_input
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
import asyncio
import inspect

logger = logging.getLogger(__name__)

# Alias für alte get_lang-Aufrufe
get_lang = get_group_language

# Sprachoptionen
LANGUAGES = {
    'de': 'Deutsch', 'en': 'English', 'es': 'Español',
    'fr': 'Français', 'it': 'Italiano', 'ru': 'Русский'
}

async def _edit_or_send(query, title, markup):
    """Versucht die vorhandene Menü-Nachricht zu ersetzen; fällt notfalls auf neue Nachricht zurück."""
    try:
        # Wichtig: erst answer(), damit alte Queries nicht ablaufen
        await query.answer()
        await query.edit_message_text(title, reply_markup=markup, disable_web_page_preview=True)
    except BadRequest as e:
        # Fallback, z. B. wenn Original kein Text war ("There is no text in the message to edit")
        # oder "message is not modified"
        try:
            await query.message.reply_text(title, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            # letzter Fallback: nur das Markup updaten (falls Text identisch)
            try:
                await query.edit_message_reply_markup(markup)
            except Exception:
                pass

def build_group_menu(cid):
    lang = get_group_language(cid) or 'de'
    status = tr('Aktiv', lang) if is_daily_stats_enabled(cid) else tr('Inaktiv', lang)
    ai_faq, ai_rss = get_ai_settings(cid)
    ai_status = "✅" if (ai_faq or ai_rss) else "❌"
    
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
         InlineKeyboardButton(f"🤖 KI {ai_status}", callback_data=f"{cid}_ai")],
        [InlineKeyboardButton(tr('📊 Statistiken', lang), callback_data=f"{cid}_stats"),
         InlineKeyboardButton(tr('❓ FAQ', lang), callback_data=f"{cid}_faq")],
        [InlineKeyboardButton(f"📊 Tagesreport {status}", callback_data=f"{cid}_toggle_stats"),
         InlineKeyboardButton(tr('🧠 Mood', lang), callback_data=f"{cid}_mood")],
        [InlineKeyboardButton(tr('🌐 Sprache', lang), callback_data=f"{cid}_language"),
         InlineKeyboardButton(tr('🗑️ Bereinigen', lang), callback_data=f"{cid}_clean_delete")],
        [InlineKeyboardButton(tr('📖 Handbuch', lang), callback_data="help"),
         InlineKeyboardButton(tr('📝 Patchnotes', lang), callback_data="patchnotes")]
    ]
    return InlineKeyboardMarkup(buttons)

async def show_group_menu(query=None, cid=None, context=None, dest_chat_id=None):
    lang = get_group_language(cid) or 'de'
    title = tr("📋 Gruppenmenü", lang)
    markup = build_group_menu(cid)

    if query:
        try:
            # Prüfen ob Message Text hat
            current_text = getattr(query.message, 'text', None) or getattr(query.message, 'caption', None)
            if current_text and current_text != title:
                await query.edit_message_text(title, reply_markup=markup)
            else:
                # Fallback: neue Nachricht senden
                await query.message.reply_text(title, reply_markup=markup)
                try:
                    await query.message.delete()
                except:
                    pass
        except BadRequest as e:
            if "no text in the message to edit" in str(e).lower():
                await query.message.reply_text(title, reply_markup=markup)
            else:
                raise
    else:
        target = dest_chat_id if dest_chat_id is not None else cid
        await context.bot.send_message(chat_id=target, text=title, reply_markup=markup)

async def _call_db_safe(fn, *args, **kwargs):
    """Sichere Ausführung von sync/async DB-Funktionen mit Logging"""
    logger.info(f"DEBUG: _call_db_safe aufgerufen: {fn.__name__} mit args: {args}")
    try:
        if inspect.iscoroutinefunction(fn):
            logger.info(f"DEBUG: {fn.__name__} ist async")
            result = await fn(*args, **kwargs)
        else:
            logger.info(f"DEBUG: {fn.__name__} ist sync, wrappe in to_thread")
            result = await asyncio.to_thread(fn, *args, **kwargs)
        logger.info(f"DEBUG: DB-Aufruf erfolgreich: {fn.__name__}")
        return result
    except Exception as e:
        logger.error(f"DEBUG: DB-Aufruf fehlgeschlagen {fn.__name__}: {e}", exc_info=True)
        raise

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    # 0) Globale Sonderfälle (ohne {cid}_...) VOR frühem Return
    if data == "group_select":
        from database import get_registered_groups
        all_groups = get_registered_groups()
        groups = await get_visible_groups(update.effective_user.id)
        if not groups:
            return await query.edit_message_text("⚠️ Keine Gruppen verfügbar.")
        kb = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid, title in groups]
        return await query.edit_message_text("Wähle eine Gruppe:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("group_"):
        _, id_str = data.split("_", 1)
        if id_str.lstrip("-").isdigit():
            cid = int(id_str)
            context.user_data["selected_chat_id"] = cid
            return await show_group_menu(query=query, cid=cid, context=context)
        else:
            return await query.answer("Ungültige Gruppen-ID.", show_alert=True)

    # 1) Einheitliches Pattern: {cid}_{func}[_sub]
    m = re.match(r'^(-?\d+)_([a-zA-Z0-9]+)(?:_(.+))?$', data)
    if not m:
        # Fallback: zurück ins aktuelle Gruppenmenü
        cid = context.user_data.get("selected_chat_id")
        if not cid:
            return await query.edit_message_text("⚠️ Keine Gruppe ausgewählt.")
        return await show_group_menu(query=query, cid=cid, context=context)

    cid  = int(m.group(1))
    func = m.group(2)
    sub  = m.group(3) if m.group(3) is not None else None
    lang = get_group_language(cid) or "de"
    back = InlineKeyboardMarkup([[InlineKeyboardButton(tr("↩️ Zurück", lang), callback_data=f"group_{cid}")]])

    # 2) help / patchnotes
    if func == "help":
        translated = translate_hybrid(HELP_TEXT, target_lang=lang)
        path = f'user_manual_{lang}.md'
        with open(path, 'w', encoding='utf-8') as f:
            f.write(translated)
        await query.message.reply_document(document=open(path, 'rb'), filename=f'Handbuch_{lang}.md')
        return await show_group_menu(query=query, cid=cid, context=context)

    if func == "patchnotes":
        notes = PATCH_NOTES if lang == 'de' else translate_hybrid(PATCH_NOTES, target_lang=lang)
        text = f"📝 <b>Patchnotes v{__version__}</b>\n\n{notes}"
        await query.message.reply_text(text, parse_mode="HTML")
        return await show_group_menu(query=query, cid=cid, context=context)

    # --- Bearbeiten-Flow aktivieren (last_edit korrigiert) ---
    elif func == "welcome" and sub == "edit":
        # Einheitliche Eingabe über ForceReply
        context.user_data['last_edit'] = (cid, 'welcome')
        set_pending_input(query.message.chat.id, update.effective_user.id, "edit",
                          {"target_chat_id": cid, "what": "welcome"})
        return await query.message.reply_text(
            tr("✏️ Sende jetzt die neue Begrüßung (Text oder Medien mit Caption).", lang),
            reply_markup=ForceReply(selective=True)
        )

    if func == "rules" and sub == "edit":
        context.user_data["last_edit"] = (cid, "rules")
        set_pending_input(query.message.chat.id, update.effective_user.id, "edit",
                          {"target_chat_id": cid, "what": "rules"})
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=tr("Bitte sende jetzt die neuen Regeln (optional mit Foto als Bild + Caption).", lang)
        )
        return

    if func == "farewell" and sub == "edit":
        context.user_data["last_edit"] = (cid, "farewell")
        set_pending_input(query.message.chat.id, update.effective_user.id, "edit",
                          {"target_chat_id": cid, "what": "farewell"})
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=tr("Bitte sende jetzt die neue Abschiedsnachricht (optional mit Foto als Bild + Caption).", lang)
        )
        return

    # 4) SUBMENÜS ZUERST
    if func in ('welcome', 'rules', 'farewell') and sub is None:
        kb = [
            [InlineKeyboardButton(tr('Bearbeiten', lang), callback_data=f"{cid}_{func}_edit"),
             InlineKeyboardButton(tr('Anzeigen', lang), callback_data=f"{cid}_{func}_show")],
            [InlineKeyboardButton(tr('Löschen', lang), callback_data=f"{cid}_{func}_delete")],
            [InlineKeyboardButton(tr('⬅ Hauptmenü', lang), callback_data=f"group_{cid}")]
        ]
        text = tr(f"⚙️ {func.capitalize()} verwalten:", lang)
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif func == 'rss' and sub is None:
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
        lines = [f"• <code>{t}</code> → {a[:30]}..." for t,a in faqs[:10]]
        ai_faq, _ = get_ai_settings(cid)
        
        help_text = (
            "❓ <b>FAQ-System</b>\n\n"
            "📝 <b>Hinzufügen:</b> <code>Trigger ⟶ Antwort</code>\n"
            "Beispiel: <code>hilfe ⟶ Für Unterstützung schreibe @admin</code>\n\n"
            "🔍 <b>Auslösung:</b> Wenn Nutzer 'hilfe' schreibt oder fragt\n\n"
            "🤖 <b>KI-Fallback:</b> Bei unbekannten Fragen automatische Antworten\n\n"
            "<b>Aktuelle FAQs:</b>\n" + 
            ("\n".join(lines) if lines else "Noch keine Einträge.")
        )
        
        kb = [
            [InlineKeyboardButton("➕ FAQ hinzufügen", callback_data=f"{cid}_faq_add"),
             InlineKeyboardButton("🗑 FAQ löschen", callback_data=f"{cid}_faq_del")],
            [InlineKeyboardButton(f"{'✅' if ai_faq else '☐'} KI-Fallback", callback_data=f"{cid}_faq_ai_toggle")],
            [InlineKeyboardButton("❓ Hilfe", callback_data=f"{cid}_faq_help")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
        ]
        return await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

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
        set_pending_input(query.message.chat.id, update.effective_user.id, "faq_add",
                      {"chat_id": cid})  

    elif func == 'faq' and sub == 'del':
        context.user_data.update(awaiting_faq_del=True, faq_group_id=cid)
        return await query.message.reply_text(
            "Bitte sende den <Trigger>, der gelöscht werden soll.",
            reply_markup=ForceReply(selective=True)
        )
        set_pending_input(query.message.chat.id, update.effective_user.id, "faq_del",
                      {"chat_id": cid})
    
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
        en, s, e, del_non_admin, warn_once, tz, hard_mode, override_until = await _call_db_safe(get_night_mode, cid)
        
        if sub == 'toggle':
            await _call_db_safe(set_night_mode, cid, enabled=not en)
            await query.answer(tr('Nachtmodus umgeschaltet.', lang), show_alert=True)
            # FIXED: Don't use update.replace() - just call the night menu directly
            query.data = f"{cid}_night"
            return await query.edit_message_text(
                f"🌙 <b>{tr('Nachtmodus', lang)}</b>\n\n"
                f"{tr('Status', lang)}: {'✅ ' + tr('Aktiv', lang) if not en else '❌ ' + tr('Inaktiv', lang)}\n"
                f"{tr('Start', lang)}: {s//60:02d}:{s%60:02d}  •  {tr('Ende', lang)}: {e//60:02d}:{e%60:02d}  •  TZ: {tz}\n"
                f"{tr('Harter Modus', lang)}: {'✅' if hard_mode else '❌'}\n"
                f"{tr('Nicht-Admin-Nachrichten löschen', lang)}: {'✅' if del_non_admin else '❌'}\n"
                f"{tr('Nur einmal pro Nacht warnen', lang)}: {'✅' if warn_once else '❌'}\n"
                f"{tr('Sofortige Ruhephase (Override) bis', lang)}: {override_until.strftime('%d.%m. %H:%M') if override_until else '–'}"
            , reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{'✅' if not en else '☐'} {tr('Aktivieren/Deaktivieren', lang)}",
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
            ]), parse_mode="HTML")
        
        elif sub == 'hard_toggle':
            await _call_db_safe(set_night_mode, cid, hard_mode=not hard_mode)
            await query.answer(tr('Harter Modus umgeschaltet.', lang), show_alert=True)
            # FIXED: Use the same approach as above
            query.data = f"{cid}_night"
            return await menu_callback(update, context)
        
        elif sub == 'del_toggle':
            await _call_db_safe(set_night_mode, cid, delete_non_admin_msgs=not del_non_admin)
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
        elif sub == 'warnonce_toggle':
            await _call_db_safe(set_night_mode, cid, warn_once=not warn_once)
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
        elif sub == 'set_start':
            context.user_data['awaiting_nm_time'] = ('start', cid)
            return await query.message.reply_text(tr('Bitte Startzeit im Format HH:MM senden (z. B. 22:00).', lang),
                                                reply_markup=ForceReply(selective=True))
            set_pending_input(query.message.chat.id, update.effective_user.id, "nm_time",
                      {"chat_id": cid, "which": "start"})
        elif sub == 'set_end':
            context.user_data['awaiting_nm_time'] = ('end', cid)
            return await query.message.reply_text(tr('Bitte Endzeit im Format HH:MM senden (z. B. 06:00).', lang),
                                                reply_markup=ForceReply(selective=True))
            set_pending_input(query.message.chat.id, update.effective_user.id, "nm_time",
                      {"chat_id": cid, "which": "end"})
        elif sub.startswith('quiet_'):
            dur_map = {'15m': 15, '1h': 60, '8h': 480}
            key = sub.split('_', 1)[1]
            minutes = dur_map.get(key)
            if minutes:
                tz = tz or "Europe/Berlin"  # Fallback für ungültige/fehlende TZ
                now = datetime.datetime.now(ZoneInfo(tz))
                set_night_mode(cid, override_until=now + datetime.timedelta(minutes=minutes))
                until = (now + datetime.timedelta(minutes=minutes)).strftime('%H:%M')
                await query.answer(tr('Ruhephase bis', lang) + f" {until}", show_alert=True)
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
        set_pending_input(query.message.chat.id, update.effective_user.id, "router_add_kw", {"chat_id": cid})
        
        if sub == 'add_dom':
            context.user_data.update(awaiting_router_add_domains=True, router_group_id=cid)
            return await query.message.reply_text("Format: <topic_id>; domain1.tld, domain2.tld",
                                                reply_markup=ForceReply(selective=True))
        set_pending_input(query.message.chat.id, update.effective_user.id, "router_add_dom", {"chat_id": cid})
        
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
        
        level_info = {
            'off': '❌ Deaktiviert',
            'light': '🟡 Leicht (20 Emojis, 10 Msgs/10s)',
            'medium': '🟠 Mittel (10 Emojis, 60/min, 6 Msgs/10s)', 
            'strict': '🔴 Streng (6 Emojis, 30/min, 4 Msgs/10s)'
        }
        
        text = (
            "🧹 <b>Spamfilter (Default / Topic 0)</b>\n\n"
            f"📊 <b>Level:</b> {level_info.get(level, level)}\n\n"
            "⚙️ <b>Funktionen:</b>\n"
            "• 📊 Emoji-Limits pro Nachricht/Minute\n"
            "• ⏱ Flood-Protection (Nachrichten/10s)\n"
            "• 🔗 Domain Whitelist/Blacklist\n"
            "• 📝 Tageslimits pro Topic & User\n"
            "• 🎯 Topic-spezifische Regeln\n\n"
            f"📈 Aktuell: {pol.get('emoji_max_per_msg', 0)} Emojis, "
            f"{pol.get('max_msgs_per_10s', 0)} Msgs/10s\n"
            f"✅ Whitelist: {len(pol.get('link_whitelist', []))} Domains\n"
            f"❌ Blacklist: {len(pol.get('domain_blacklist', []))} Domains"
        )
        
        kb = [
            [InlineKeyboardButton("📊 Level ändern", callback_data=f"{cid}_spam_lvl_cycle")],
            [InlineKeyboardButton("📝 Whitelist", callback_data=f"{cid}_spam_wl_edit"),
             InlineKeyboardButton("❌ Blacklist", callback_data=f"{cid}_spam_bl_edit")],
            [InlineKeyboardButton("🎯 Topic-Regeln", callback_data=f"{cid}_spam_tsel")],
            [InlineKeyboardButton("❓ Hilfe", callback_data=f"{cid}_spam_help")],
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
        page = int(sub.split('_',1)[1])
        return await query.edit_message_reply_markup(reply_markup=_topics_keyboard(cid, page, purpose="spam"))

    elif func == 'spam' and sub and sub.startswith('t_'):
        topic_id = int(sub.split('_',1)[1])
        # vorher: pol = get_spam_policy_topic(cid, topic_id) or {}
        # Sicherstellen, dass topic_id kein None ist (DB-Funktionen erwarten 0 für "kein Topic")
        if topic_id is None:
            topic_id = 0
        try:
            pol = get_spam_policy_topic(cid, topic_id) or {}
        except IndexError:
            # Defensive Fallback: wenn die DB unerwartete Spalten/Zeilen zurückgibt,
            # abbrechen und mit leerer Policy weiterarbeiten statt das Menü abstürzen zu lassen.
            pol = {}
        level = pol.get('level','off'); emsg = pol.get('emoji_max_per_msg',0) or 0; rate = pol.get('max_msgs_per_10s',0) or 0
        wl = ", ".join(pol.get('link_whitelist') or []) or "–"
        bl = ", ".join(pol.get('domain_blacklist') or []) or "–"
        limit = pol.get('per_user_daily_limit', 0) or 0
        qmode = (pol.get('quota_notify') or 'smart')
        text = (
            f"🧹 <b>Spamfilter – Topic {topic_id}</b>\n\n"
            f"Level: <b>{level}</b>\n"
            f"Emoji/Msg: <b>{emsg}</b> • Flood/10s: <b>{rate}</b>\n"
            f"Limit/Tag/User: <b>{limit}</b>\n"
            f"Rest-Info: <b>{qmode}</b>\n"
            f"Whitelist: {wl}\nBlacklist: {bl}"
        )
        kb = [
            [InlineKeyboardButton("Level ⏭", callback_data=f"{cid}_spam_setlvl_{topic_id}")],
            [InlineKeyboardButton("Emoji −", callback_data=f"{cid}_spam_emj_-_{topic_id}"),
            InlineKeyboardButton("Emoji +", callback_data=f"{cid}_spam_emj_+_{topic_id}")],
            [InlineKeyboardButton("Flood −", callback_data=f"{cid}_spam_rate_-_{topic_id}"),
            InlineKeyboardButton("Flood +", callback_data=f"{cid}_spam_rate_+_{topic_id}")],
            [InlineKeyboardButton("Whitelist bearbeiten", callback_data=f"{cid}_spam_wl_edit_{topic_id}"),
            InlineKeyboardButton("Blacklist bearbeiten", callback_data=f"{cid}_spam_bl_edit_{topic_id}")],
            [InlineKeyboardButton("Limit/Tag setzen", callback_data=f"{cid}_spam_limt_edit_{topic_id}")],
            [InlineKeyboardButton("Benachrichtigung ⏭", callback_data=f"{cid}_spam_qmode_{topic_id}")],
            [InlineKeyboardButton("↩️ Zurück (Topics)", callback_data=f"{cid}_spam_tsel")]
        ]
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    # Linksperre Submenü
    elif func == 'linkprot' and sub is None:
        prot_on, warn_on, warn_text, except_on = get_link_settings(cid)
        kb = [
            [InlineKeyboardButton(f"{'✅' if prot_on else '☐'} {tr('Linkschutz aktiv', lang)}", callback_data=f"{cid}_linkprot_toggle")],
            [InlineKeyboardButton(f"{'✅' if warn_on else '☐'} {tr('Warnhinweis senden', lang)}", callback_data=f"{cid}_linkprot_warn_toggle")],
            [InlineKeyboardButton(f"{'✅' if except_on else '☐'} {tr('Ausnahmen (Topic-Owner)', lang)}", callback_data=f"{cid}_linkprot_exc_toggle")],
            [InlineKeyboardButton(tr('Warntext ändern', lang), callback_data=f"{cid}_linkprot_edit")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
        ]
        text = tr('🔗 Linkschutz – Einstellungen', lang)
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif func == 'linkprot':
        # Aktionen + direktes Neu-Rendern des Submenüs (ohne rekursive data-Manipulation)
        if sub == "edit":
            context.user_data['awaiting_link_warn'] = True
            context.user_data['link_warn_group'] = cid
            return await query.message.reply_text(tr("Sende jetzt deinen neuen Warn-Text:", lang),
                                                  reply_markup=ForceReply(selective=True))
            set_pending_input(query.message.chat.id, update.effective_user.id, "link_warn",
                      {"chat_id": cid})

        elif sub in ("toggle", "warn_toggle", "exc_toggle"):
            prot_on, warn_on, warn_text, except_on = get_link_settings(cid)
            if sub == "toggle":
                set_link_settings(cid, protection=not prot_on)
            elif sub == "warn_toggle":
                set_link_settings(cid, warning_on=not warn_on)
            elif sub == "exc_toggle":
                set_link_settings(cid, exceptions_on=not except_on)
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
            # aktualisierte Werte laden und Submenü neu anzeigen
            prot_on, warn_on, warn_text, except_on = get_link_settings(cid)
            kb = [
                [InlineKeyboardButton(f"{'✅' if prot_on else '☐'} {tr('Linkschutz aktiv', lang)}", callback_data=f"{cid}_linkprot_toggle")],
                [InlineKeyboardButton(f"{'✅' if warn_on else '☐'} {tr('Warnhinweis senden', lang)}", callback_data=f"{cid}_linkprot_warn_toggle")],
                [InlineKeyboardButton(f"{'✅' if except_on else '☐'} {tr('Ausnahmen (Topic-Owner)', lang)}", callback_data=f"{cid}_linkprot_exc_toggle")],
                [InlineKeyboardButton(tr('Warntext ändern', lang), callback_data=f"{cid}_linkprot_edit")],
                [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
            ]
            text = tr('🔗 Linkschutz – Einstellungen', lang)
            return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # KI-Submenü hinzufügen
    elif func == 'ai' and sub is None:
        ai_faq, ai_rss = get_ai_settings(cid)
        text = (
            "🤖 <b>KI-Einstellungen</b>\n\n"
            "🎯 <b>Verfügbare Features:</b>\n"
            f"• FAQ-Fallback: {'✅' if ai_faq else '❌'}\n"
            f"• RSS-Zusammenfassung: {'✅' if ai_rss else '❌'}\n\n"
            "🔮 <b>Geplante Features:</b>\n"
            "• KI-Moderation (Chat-Analyse)\n"
            "• Sentiment-Erkennung\n"
            "• Auto-Antworten auf Fragen"
        )
        kb = [
            [InlineKeyboardButton(f"{'✅' if ai_faq else '☐'} FAQ-Fallback", callback_data=f"{cid}_ai_faq_toggle")],
            [InlineKeyboardButton(f"{'✅' if ai_rss else '☐'} RSS-Zusammenfassung", callback_data=f"{cid}_ai_rss_toggle")],
            [InlineKeyboardButton("🔮 KI-Moderation (Beta)", callback_data=f"{cid}_ai_moderation")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
        ]
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    
    # 5) DANACH erst die Sub-Aktionen…
    if func and sub:
        # Welcome/Rules/Farewell Aktionen
        if func in ('welcome', 'rules', 'farewell'):
            get_map = {'welcome': get_welcome, 'rules': get_rules, 'farewell': get_farewell}
            set_map = {'welcome': set_welcome, 'rules': set_rules, 'farewell': set_farewell}
            del_map = {'welcome': delete_welcome, 'rules': delete_rules, 'farewell': delete_farewell}
            
            if sub == 'show' and func in get_map:
                rec = get_map[func](cid)
                logger.debug(f"Retrieved {func} record: {rec}")
                if rec:
                    text = rec[1] if len(rec) > 1 else ''
                    media = rec[0] if len(rec) > 0 else None
                    if media:
                        logger.debug(f"Media found: {media} (type: {type(media)})")
                        try:
                            await query.message.reply_photo(
                                photo=media,
                                caption=text,
                                reply_markup=back,
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger.error(f"Error sending photo: {e}")
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
                context.user_data['last_edit'] = (cid, func)
                label = {'welcome':'Begrüßung','rules':'Regeln','farewell':'Abschied'}[func]
                return await query.message.reply_text(
                    f"✏️ Sende nun die neue {label}:",
                    reply_markup=ForceReply(selective=True)
                )

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
                    await query.answer('❗ Kein RSS-Topic gesetzt. Bitte erst /settopicrss ausführen.', show_alert=True)
                    return await show_group_menu(query=query, cid=cid, context=context)
                
                # Flags setzen
                context.user_data.pop('awaiting_mood_question', None)
                context.user_data.pop('last_edit', None)
                context.user_data.update(awaiting_rss_url=True, rss_group_id=cid)
                set_pending_input(query.message.chat.id, update.effective_user.id, "rss_url",
                                  {"target_chat_id": cid})
                # Neue Nachricht mit ForceReply
                await query.message.reply_text(
                    '📰 Bitte sende die RSS-URL:',
                    reply_markup=ForceReply(selective=True)
                )
                await query.answer("Sende nun die RSS-URL als Antwort.")
                return
            elif sub == 'list':
                feeds = db_list_rss_feeds(cid)
                if not feeds:
                    text = 'Keine RSS-Feeds.'
                else:
                    feed_strings = []
                    for feed in feeds:
                        if isinstance(feed, tuple):
                            if len(feed) >= 2:
                                feed_strings.append(f"{feed[1]}: {feed[0]}")
                            else:
                                feed_strings.append(str(feed[0]))
                        else:
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
                    set_pending_input(query.message.chat.id, update.effective_user.id, "spam_edit",
                          {"chat_id": cid, "topic_id": topic_id, "which": "whitelist"})
                else:
                    context.user_data.update(awaiting_spam_blacklist=True, spam_group_id=cid, spam_topic_id=topic_id)
                    return await query.message.reply_text("Sende Blacklist-Domains, Komma-getrennt:", reply_markup=ForceReply(selective=True))
                    set_pending_input(query.message.chat.id, update.effective_user.id, "spam_edit",
                          {"chat_id": cid, "topic_id": topic_id, "which": "blacklist"})

        elif func == 'spam' and sub and sub.startswith('limt_edit_'):
            topic_id = int(sub.split('_')[-1])
            context.user_data.update(awaiting_topic_limit=True, spam_group_id=cid, spam_topic_id=topic_id)
            return await query.message.reply_text("Bitte Limit/Tag/User als Zahl senden (0 = aus):", reply_markup=ForceReply(selective=True))
            set_pending_input(query.message.chat.id, update.effective_user.id, "spam_edit",
                          {"chat_id": cid, "topic_id": topic_id, "which": "limit"})

        elif func == 'spam' and sub and sub.startswith('qmode_'):
            topic_id = int(sub.split('_')[-1])
            pol = get_spam_policy_topic(cid, topic_id) or {'quota_notify':'smart'}
            order = ['off','smart','always']
            cur = (pol.get('quota_notify') or 'smart').lower()
            nxt = order[(order.index(cur)+1) % len(order)]
            set_spam_policy_topic(cid, topic_id, quota_notify=nxt)
            await query.answer(f"Rest-Info: {nxt}", show_alert=True)
            update.callback_query.data = f"{cid}_spam_t_{topic_id}"
            return await menu_callback(update, context)

        elif func == 'router' and sub in ('tsel_kw','tsel_dom'):
            purpose = 'router_kw' if sub.endswith('kw') else 'router_dom'
            return await query.edit_message_text(
                "🧭 <b>Router: Ziel-Topic wählen</b>",
                reply_markup=_topics_keyboard(cid, page=0, purpose=purpose),
                parse_mode="HTML"
            )

        elif func in ('router_kw','router_dom') and sub and sub.startswith('tp_'):
            page = int(sub.split('_',1)[1])
            return await query.edit_message_reply_markup(
                reply_markup=_topics_keyboard(cid, page=page, purpose=func)
            )

        elif func == 'router' and sub and (sub.startswith('pick_kw_') or sub.startswith('pick_dom_')):
            topic_id = int(sub.split('_')[-1])
            if 'pick_kw_' in sub:
                context.user_data.update(awaiting_router_add_keywords=True, router_group_id=cid, router_target_tid=topic_id)
                return await query.message.reply_text("Sende Keywords (Komma-getrennt) für die Regel:", reply_markup=ForceReply(selective=True))
                set_pending_input(query.message.chat.id, update.effective_user.id, "router_add_kw",
                          {"chat_id": cid, "target_tid": topic_id})
            else:
                context.user_data.update(awaiting_router_add_domains=True, router_group_id=cid, router_target_tid=topic_id)
                return await query.message.reply_text("Sende Domains (Komma-getrennt) für die Regel:", reply_markup=ForceReply(selective=True))
                set_pending_input(query.message.chat.id, update.effective_user.id, "router_add_dom",
                          {"chat_id": cid, "target_tid": topic_id})

        # === Sprachcode setzen ===
        elif func == 'setlang' and sub:
            lang_code = sub
            set_group_language(cid, lang_code)
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

    # 6) DANACH die Einzelfunktionen…
    if func == 'toggle' and sub == 'stats':
        cur = is_daily_stats_enabled(cid)
        set_daily_stats(cid, not cur)
        await query.answer(tr(f"Tagesstatistik {'aktiviert' if not cur else 'deaktiviert'}", lang), show_alert=True)
        return await show_group_menu(query=query, cid=cid, context=context)

    elif func == 'clean' and sub == 'delete':
        await query.answer('⏳ Bereinige…')
        try:
            print(f"DEBUG: Starting clean_delete for chat_id={cid}")
            removed = await clean_delete_accounts_for_chat(cid, context.bot)
            text = f"✅ {removed} Accounts entfernt."
            return await query.edit_message_text(text, reply_markup=back)
        except Exception as e:
            print(f"ERROR in clean_delete: {str(e)}")
            error_text = f"⚠️ Fehler bei der Bereinigung: {str(e)}"
            return await query.edit_message_text(error_text, reply_markup=back)

    elif func == 'stats' and sub == 'export':
        return await export_stats_csv_command(update, context)

    elif func == 'stats' and not sub:
        context.user_data['stats_group_id'] = cid
        return await stats_command(update, context)

    # Mood-Frage ändern (korrigierter Handler)
    elif func == 'edit' and sub == 'mood_q':
        context.user_data['awaiting_mood_question'] = True
        context.user_data['mood_group_id'] = cid
        context.user_data.pop('awaiting_rss_url', None)
        context.user_data.pop('last_edit', None)
        
        # Neue Nachricht statt Edit
        await query.message.reply_text(
            tr('Bitte sende deine neue Mood-Frage:', lang),
            reply_markup=ForceReply(selective=True)
        )
        set_pending_input(query.message.chat.id, update.effective_user.id, "mood_q",
                      {"chat_id": cid})
        await query.answer()
        return

    # Nachtmodus-Zeit-Eingaben korrigieren:
    elif sub == "set_start":
        context.user_data['awaiting_nm_time'] = ('start', cid)
        await query.message.reply_text(
            tr('Bitte Startzeit im Format HH:MM senden (z. B. 22:00).', lang),
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()
        return

    elif sub == "set_end":
        context.user_data['awaiting_nm_time'] = ('end', cid)
        await query.message.reply_text(
            tr('Bitte Endzeit im Format HH:MM senden (z. B. 06:00).', lang),
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()
        return

    # Fallback: Hauptmenü
    cid = context.user_data.get('selected_chat_id')
    if cid:
        return await show_group_menu(query=query, cid=cid, context=context)

async def menu_free_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = (msg.text or msg.caption or "").strip()
    photo_id = msg.photo[-1].file_id if msg.photo else None
    doc_id = msg.document.file_id if msg.document else None
    media_id = photo_id or doc_id
    
    # --- DB-Pendings laden (Fallback nach Neustart/Crash) ---
    pend = get_pending_inputs(msg.chat.id, update.effective_user.id) or {}

    # last_edit-Fallback (Welcome/Rules/Farewell)
    if 'last_edit' not in context.user_data and 'edit' in pend:
        payload = pend.get('edit') or {}
        if isinstance(payload, dict) and payload.get('chat_id') and payload.get('what'):
            context.user_data['last_edit'] = (int(payload['chat_id']), payload['what'])
            clear_pending_input(msg.chat.id, update.effective_user.id, 'edit')
        
    # DEBUG: Log alles
    logger.info(f"DEBUG: Handler aufgerufen mit Text: '{text}'")
    logger.info(f"DEBUG: user_data: {context.user_data}")
    logger.info(f"DEBUG: Media: photo={bool(photo_id)}, doc={bool(doc_id)}")
    
    # --- DB-Fallback: offener Edit-Flow?
    if 'last_edit' not in context.user_data:
        pend = get_pending_input(msg.chat.id, update.effective_user.id, "edit")
        if pend and isinstance(pend, dict) and pend.get("what") and pend.get("target_chat_id"):
            context.user_data['last_edit'] = (int(pend["target_chat_id"]), pend["what"])
            clear_pending_input(msg.chat.id, update.effective_user.id, "edit")

    # 'last_edit' Handler
    if 'last_edit' in context.user_data:
        last_edit = context.user_data.pop('last_edit')
        logger.info(f"DEBUG: last_edit gefunden: {last_edit}")
        
        if isinstance(last_edit, tuple) and len(last_edit) == 2:
            cid, what = last_edit
            logger.info(f"DEBUG: Verarbeite {what} für Chat {cid}")
            logger.info(f"DEBUG: Speichere - media_id: {media_id}, text: '{text}'")
            
            try:
                if what == 'welcome':
                    logger.info(f"DEBUG: Rufe set_welcome auf: {cid}, {media_id}, '{text}'")
                    await _call_db_safe(set_welcome, cid, media_id, text)
                    logger.info("DEBUG: set_welcome erfolgreich")
                    return await msg.reply_text("✅ Begrüßung gespeichert.")
                elif what == 'rules':
                    logger.info(f"DEBUG: Rufe set_rules auf: {cid}, {media_id}, '{text}'")
                    await _call_db_safe(set_rules, cid, media_id, text)
                    return await msg.reply_text("✅ Regeln gespeichert.")
                elif what == 'farewell':
                    logger.info(f"DEBUG: Rufe set_farewell auf: {cid}, {media_id}, '{text}'")
                    await _call_db_safe(set_farewell, cid, media_id, text)
                    return await msg.reply_text("✅ Abschied gespeichert.")
            except Exception as e:
                logger.error(f"DEBUG: Fehler beim Speichern von {what}: {e}", exc_info=True)
                return await msg.reply_text(f"⚠️ Fehler beim Speichern: {e}")
        else:
            logger.warning(f"DEBUG: Fehlerhaftes Format für last_edit: {last_edit}")
    else:
        logger.info("DEBUG: Kein 'last_edit' in user_data gefunden")
    
    # Nachtmodus-Zeit setzen (AWAIT hinzufügen)
    if 'awaiting_nm_time' in context.user_data:
        sub, cid = context.user_data.pop('awaiting_nm_time')
        try:
            hh, mm = map(int, text.split(":", 1))
            if sub == 'start':
                await _call_db_safe(set_night_mode, cid, start_minute=hh * 60 + mm)
                await msg.reply_text("✅ Startzeit gespeichert.")
            else:
                await _call_db_safe(set_night_mode, cid, end_minute=hh * 60 + mm)
                await msg.reply_text("✅ Endzeit gespeichert.")
        except (ValueError, IndexError):
            await msg.reply_text("⚠️ Ungültiges Format. Bitte nutze HH:MM.")
        return

    # Linksperre-Warntext (AWAIT hinzufügen)
    if context.user_data.pop('awaiting_link_warn', False) or ('link_warn' in pend):
        cid = context.user_data.pop('link_warn_group', (pend.get('link_warn') or {}).get('chat_id'))
        await _call_db_safe(set_link_settings, cid, warning_text=text)
        clear_pending_input(msg.chat.id, update.effective_user.id, 'link_warn')
        return await msg.reply_text("✅ Warn-Text gespeichert.")

    # Mood-Frage speichern (AWAIT hinzufügen)
    if context.user_data.pop('awaiting_mood_question', False) or ('mood_q' in pend):
        cid = context.user_data.pop('mood_group_id', (pend.get('mood_q') or {}).get('chat_id'))
        if not cid:
            return await msg.reply_text("⚠️ Keine Gruppe ausgewählt.")
        try:
            await _call_db_safe(set_mood_question, cid, text)
            return await msg.reply_text("✅ Mood-Frage gespeichert.")
        except Exception as e:
            logger.error(f"Fehler beim Speichern der Mood-Frage: {e}")
            return await msg.reply_text(f"⚠️ Fehler beim Speichern der Mood-Frage: {e}")
            clear_pending_input(msg.chat.id, update.effective_user.id, 'mood_q')
            
    # Weitere Handler mit AWAIT...
    if context.user_data.pop('awaiting_spam_whitelist', False) or ((pend.get('spam_edit') or {}).get('which') == 'whitelist'):
        cid = context.user_data.pop('spam_group_id', (pend.get('spam_edit') or {}).get('chat_id'))
        tid = context.user_data.pop('spam_topic_id', (pend.get('spam_edit') or {}).get('topic_id', 0))
        wl = [d.strip().lower() for d in text.split(",") if d.strip()]
        await _call_db_safe(set_spam_policy_topic, cid, tid, link_whitelist=wl)
        clear_pending_input(msg.chat.id, update.effective_user.id, 'spam_edit')
        return await msg.reply_text(f"✅ Whitelist gespeichert (Topic {tid}).")

    if context.user_data.pop('awaiting_spam_blacklist', False)  or ((pend.get('spam_edit') or {}).get('which') == 'blacklist'):
        cid = context.user_data.pop('spam_group_id', (pend.get('spam_edit') or {}).get('chat_id'))
        tid = context.user_data.pop('spam_topic_id', (pend.get('spam_edit') or {}).get('topic_id', 0))
        bl = [d.strip().lower() for d in text.split(",") if d.strip()]
        await _call_db_safe(set_spam_policy_topic, cid, tid, domain_blacklist=bl)
        clear_pending_input(msg.chat.id, update.effective_user.id, 'spam_edit')
        return await msg.reply_text(f"✅ Blacklist gespeichert (Topic {tid}).")

    # 4) Router: add keywords (mit Ziel-Topic)
    if context.user_data.pop('awaiting_router_add_keywords', False) or ('router_add_kw' in pend):
        cid = context.user_data.pop('router_group_id', (pend.get('router_add_kw') or {}).get('chat_id'))
        tid = context.user_data.pop('router_target_tid', (pend.get('router_add_kw') or {}).get('target_tid'))
        kws = [w.strip() for w in text.split(",") if w.strip()]
        if not tid or not kws:
            return await msg.reply_text("Bitte Keywords angeben.")
        rid = add_topic_router_rule(cid, tid, keywords=kws)
        clear_pending_input(msg.chat.id, update.effective_user.id, 'router_add_kw')
        return await msg.reply_text(f"✅ Regel #{rid} → Topic {tid} (Keywords) angelegt.")

    # 5) Router: add domains (mit Ziel-Topic)
    if context.user_data.pop('awaiting_router_add_domains', False) or ('router_add_dom' in pend):
        cid = context.user_data.pop('router_group_id', (pend.get('router_add_dom') or {}).get('chat_id'))
        tid = context.user_data.pop('router_target_tid', (pend.get('router_add_dom') or {}).get('target_tid'))
        doms = [d.strip().lower() for d in text.split(",") if d.strip()]
        if not tid or not doms:
            return await msg.reply_text("Bitte Domains angeben.")
        rid = add_topic_router_rule(cid, tid, domains=doms)
        clear_pending_input(msg.chat.id, update.effective_user.id, 'router_add_dom')
        return await msg.reply_text(f"✅ Regel #{rid} → Topic {tid} (Domains) angelegt.")
    # 6) Router: delete
    if context.user_data.pop('awaiting_router_delete', False) or ('router_delete' in pend):
        cid = context.user_data.pop('router_group_id', (pend.get('router_delete') or {}).get('chat_id'))
        if not text.isdigit():
            return await msg.reply_text("Bitte eine numerische Regel-ID senden.")
        delete_topic_router_rule(cid, int(text))
        clear_pending_input(msg.chat.id, update.effective_user.id, 'router_delete')
        return await msg.reply_text("🗑 Regel gelöscht.")
    # 7) Router: toggle
    if context.user_data.pop('awaiting_router_toggle', False) or ('router_toggle' in pend):
        cid = context.user_data.pop('router_group_id', (pend.get('router_toggle') or {}).get('chat_id'))
        m = re.match(r'^\s*(\d+)\s+(on|off)\s*$', text, re.I)
        if not m:
            return await msg.reply_text("Format: <regel_id> on|off")
        rid = int(m.group(1)); on = m.group(2).lower() == "on"
        toggle_topic_router_rule(cid, rid, on)
        clear_pending_input(msg.chat.id, update.effective_user.id, 'router_toggle')
        return await msg.reply_text("🔁 Regel umgeschaltet.")
    # 8) FAQ add
    if context.user_data.pop('awaiting_faq_add', False) or ('faq_add' in pend):
        cid = context.user_data.pop('faq_group_id', (pend.get('faq_add') or {}).get('chat_id'))
        if "⟶" not in text and "->" not in text:
            return await msg.reply_text("Bitte im Format <Trigger> ⟶ <Antwort> senden.")
        clear_pending_input(msg.chat.id, update.effective_user.id, 'faq_add')
        splitter = "⟶" if "⟶" in text else "->"
        trig, ans = [p.strip() for p in text.split(splitter, 1)]
        upsert_faq(cid, trig, ans)
        return await msg.reply_text("✅ FAQ gespeichert.")

    # 9) FAQ delete
    if context.user_data.pop('awaiting_faq_del', False) or ('faq_del' in pend):
        cid = context.user_data.pop('faq_group_id', (pend.get('faq_del') or {}).get('chat_id'))
        delete_faq(cid, text.strip())
        return await msg.reply_text("🗑 FAQ gelöscht (falls vorhanden).")
        clear_pending_input(msg.chat.id, update.effective_user.id, 'faq_del')
        
    if context.user_data.pop('awaiting_topic_limit', False):
        cid = context.user_data.pop('spam_group_id')
        tid = context.user_data.pop('spam_topic_id')
        try:
            limit = int((update.effective_message.text or "").strip())
        except:
            return await update.effective_message.reply_text("Bitte eine Zahl senden.")
        set_spam_policy_topic(cid, tid, per_user_daily_limit=max(0, limit))
        return await update.effective_message.reply_text(f"✅ Limit gesetzt: {limit}/Tag/User (Topic {tid}).")

    # Whitelist/Blacklist (Spam)
    if context.user_data.pop('awaiting_spam_whitelist', False) or (pend.get('spam_edit',{}).get('which')=='whitelist'):
        cid = context.user_data.pop('spam_group_id', pend.get('spam_edit',{}).get('chat_id'))
        tid = context.user_data.pop('spam_topic_id', pend.get('spam_edit',{}).get('topic_id',0))
        doms = [d.strip().lower() for d in re.split(r"[,\s]+", text) if d.strip()]
        cur = get_spam_policy_topic(cid, tid) or {}
        cur.setdefault("link_whitelist", [])
        cur["link_whitelist"] = sorted(set(cur["link_whitelist"] + doms))
        set_spam_policy_topic(cid, tid, link_whitelist=cur["link_whitelist"])
        clear_pending_input(cid, update.effective_user.id, "spam_edit")
        return await msg.reply_text(f"✅ Whitelist aktualisiert ({len(cur['link_whitelist'])} Domains).")

    if context.user_data.pop('awaiting_spam_blacklist', False) or (pend.get('spam_edit',{}).get('which')=='blacklist'):
        cid = context.user_data.pop('spam_group_id', pend.get('spam_edit',{}).get('chat_id'))
        tid = context.user_data.pop('spam_topic_id', pend.get('spam_edit',{}).get('topic_id',0))
        doms = [d.strip().lower() for d in re.split(r"[,\s]+", text) if d.strip()]
        cur = get_spam_policy_topic(cid, tid) or {}
        cur.setdefault("domain_blacklist", [])
        cur["domain_blacklist"] = sorted(set(cur["domain_blacklist"] + doms))
        set_spam_policy_topic(cid, tid, domain_blacklist=cur["domain_blacklist"])
        clear_pending_input(cid, update.effective_user.id, "spam_edit")
        return await msg.reply_text(f"✅ Blacklist aktualisiert ({len(cur['domain_blacklist'])} Domains).")

    # Router: Keywords / Domains / Delete / Toggle – analog (gekürzt):
    if context.user_data.pop('awaiting_router_add_keywords', False) or ('router_add_kw' in pend):
        cid = context.user_data.pop('router_group_id', pend.get('router_add_kw',{}).get('chat_id'))
        kws = [w.strip().lower() for w in re.split(r"[,\s]+", text) if w.strip()]
        for kw in kws: add_topic_router_rule(cid, keyword=kw)
        clear_pending_input(cid, update.effective_user.id, "router_add_kw")
        return await msg.reply_text(f"✅ {len(kws)} Keyword(s) hinzugefügt.")

    if context.user_data.pop('awaiting_router_add_domains', False) or ('router_add_dom' in pend):
        cid = context.user_data.pop('router_group_id', pend.get('router_add_dom',{}).get('chat_id'))
        doms = [d.strip().lower() for d in re.split(r"[,\s]+", text) if d.strip()]
        for d in doms: add_topic_router_rule(cid, domain=d)
        clear_pending_input(cid, update.effective_user.id, "router_add_dom")
        return await msg.reply_text(f"✅ {len(doms)} Domain(s) hinzugefügt.")

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
    # Callback Handler (Gruppe 0 - Commands haben Vorrang)
    app.add_handler(CallbackQueryHandler(menu_callback), group=0)
    
    # Reply Handler (Gruppe 1) - COMMANDS AUSSCHLIESSEN
    app.add_handler(MessageHandler(
        filters.REPLY 
        & (filters.TEXT | filters.PHOTO | filters.Document.ALL)
        & ~filters.COMMAND  # <- WICHTIG: Commands ausschließen
        & (filters.ChatType.GROUPS | filters.ChatType.PRIVATE),
        menu_free_text_handler
    ), group=1)

