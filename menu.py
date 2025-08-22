# menu.py – Greeny Group Manager
# ------------------------------------------------------------
# Zweck:
# - Zentrales Inline-Menü für Gruppenverwaltung (Willkommen/Regeln/Abschied,
#   Captcha, Spamfilter, Nachtmodus, Topic-Router, RSS, FAQ, KI, Stats, etc.)
# - Einheitliche, robuste Callback-Logik
# - Klare Abschnitts-Kommentare und konsistentes Error-Handling
# ------------------------------------------------------------

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Update
from telegram.ext import CallbackQueryHandler, filters, MessageHandler, ContextTypes
from telegram.error import BadRequest
import re
import logging
import datetime
import asyncio
import inspect
from zoneinfo import ZoneInfo

# -----------------------------
# DB/Service-Importe (bereinigt)
# -----------------------------
from database import (
    get_link_settings, set_link_settings,
    get_welcome, set_welcome, delete_welcome,
    get_rules, set_rules, delete_rules,
    get_captcha_settings, set_captcha_settings,
    get_farewell, set_farewell, delete_farewell,
    get_rss_topic, list_rss_feeds as db_list_rss_feeds, remove_rss_feed,
    get_ai_settings, set_ai_settings,
    is_daily_stats_enabled, set_daily_stats,
    get_mood_question, set_mood_question, get_mood_topic,
    list_faqs, upsert_faq, delete_faq,
    get_group_language, set_group_language,
    list_forum_topics, count_forum_topics,
    get_night_mode, set_night_mode,
    set_pending_input, get_pending_inputs, get_pending_input, clear_pending_input,
    get_rss_feed_options, set_spam_policy_topic, get_spam_policy_topic,
    effective_ai_mod_policy, get_ai_mod_settings, set_ai_mod_settings,
    top_strike_users, list_topic_router_rules
)
from access import get_visible_groups
from statistic import stats_command, export_stats_csv_command, log_feature_interaction
from utils import clean_delete_accounts_for_chat, tr
from translator import translate_hybrid
from patchnotes import PATCH_NOTES, __version__
from user_manual import HELP_TEXT

logger = logging.getLogger(__name__)

# ----------------------------------------
# Aliase & Konstanten für Sprachbehandlung
# ----------------------------------------
get_lang = get_group_language
LANGUAGES = {
    'de': 'Deutsch', 'en': 'English', 'es': 'Español',
    'fr': 'Français', 'it': 'Italiano', 'ru': 'Русский'
}

TOPICS_PAGE_SIZE = 10

# ============================================================
# Hilfsfunktionen (Senden/Edits, DB-Safe, Keyboard-Bausteine)
# ============================================================

async def _edit_or_send(query, title, markup):
    """Versuche die vorhandene Menü-Nachricht zu ersetzen; bei Fehler neue senden."""
    try:
        await query.answer()
        await query.edit_message_text(title, reply_markup=markup, disable_web_page_preview=True)
    except BadRequest:
        try:
            await query.message.reply_text(title, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            try:
                await query.edit_message_reply_markup(markup)
            except Exception:
                pass

async def _call_db_safe(fn, *args, **kwargs):
    """Sichere Ausführung von sync/async DB-Funktionen mit Logging."""
    try:
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)
    except Exception as e:
        logger.error(f"DB-Aufruf fehlgeschlagen: {fn.__name__}: {e}", exc_info=True)
        raise

def _topics_keyboard(cid: int, page: int, cb_prefix: str):
    """
    Generischer Topic-Auswahldialog.
    cb_prefix bestimmt das Callback-Muster, z.B.:
      - f\"{cid}_spam_t_{{topic_id}}\"
      - f\"{cid}_router_pick_kw_{{topic_id}}\"
      - f\"{cid}_router_pick_dom_{{topic_id}}\"
      - f\"{cid}_aimod_topic_{{topic_id}}\"
    """
    offset = page * TOPICS_PAGE_SIZE
    rows = list_forum_topics(cid, limit=TOPICS_PAGE_SIZE, offset=offset)
    total = count_forum_topics(cid)
    kb = []

    for topic_id, name, _ in rows:
        kb.append([InlineKeyboardButton(name[:56], callback_data=f"{cb_prefix}{topic_id}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"{cid}_tpnav_{cb_prefix}{page-1}"))
    if offset + TOPICS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"{cid}_tpnav_{cb_prefix}{page+1}"))
    if nav:
        kb.append(nav)

    kb.append([InlineKeyboardButton("↩️ Zurück", callback_data=f"group_{cid}")])
    return InlineKeyboardMarkup(kb)

def build_group_menu(cid: int):
    lang = get_group_language(cid) or 'de'
    status = tr('Aktiv', lang) if is_daily_stats_enabled(cid) else tr('Inaktiv', lang)
    ai_faq, ai_rss = get_ai_settings(cid)
    ai_status = "✅" if (ai_faq or ai_rss) else "❌"

    buttons = [
        [InlineKeyboardButton(tr('Begrüßung', lang), callback_data=f"{cid}_welcome"),
         InlineKeyboardButton(tr('🔐 Captcha', lang), callback_data=f"{cid}_captcha")],
        [InlineKeyboardButton(tr('Regeln', lang), callback_data=f"{cid}_rules"),
         InlineKeyboardButton(tr('Abschied', lang), callback_data=f"{cid}_farewell")],
        [InlineKeyboardButton(tr('🧹 Spamfilter', lang), callback_data=f"{cid}_spam")],
        [InlineKeyboardButton(tr('🌙 Nachtmodus', lang), callback_data=f"{cid}_night"),
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
        await _edit_or_send(query, title, markup)
        return
    target = dest_chat_id if dest_chat_id is not None else cid
    await context.bot.send_message(chat_id=target, text=title, reply_markup=markup)

async def _render_rss_list(query, cid, lang=None):
    lang = lang or (get_group_language(cid) or "de")
    feeds = db_list_rss_feeds(cid) or []
    if not feeds:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('↩️ Zurück', callback_data=f'group_{cid}')]])
        return await query.edit_message_text('Keine RSS-Feeds.', reply_markup=kb)

    rows = []
    text_lines = ["Aktive Feeds:"]
    for item in feeds:
        url = item[0]
        tid = item[1] if len(item) > 1 else "?"
        opts = get_rss_feed_options(cid, url) or {}
        img_on = bool(opts.get("post_images", False))
        text_lines.append(f"- {url} (Topic {tid})")
        rows.append([
            InlineKeyboardButton(f"🖼 Bilder: {'AN' if img_on else 'AUS'}", callback_data=f"{cid}_rss_img_toggle|{url}"),
            InlineKeyboardButton("🗑 Entfernen", callback_data=f"{cid}_rss_del|{url}")
        ])
    rows.append([InlineKeyboardButton('↩️ Zurück', callback_data=f'group_{cid}')])
    return await query.edit_message_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(rows))

async def _render_spam_root(query, cid, lang=None):
    lang = lang or (get_group_language(cid) or "de")
    pol = get_spam_policy_topic(cid, 0) or {}
    level = pol.get('level', 'off')
    level_info = {
        'off': '❌ Deaktiviert',
        'light': '🟡 Leicht (20 Emojis, 10 Msgs/10s)',
        'medium': '🟠 Mittel (10 Emojis, 60/min, 6 Msgs/10s)',
        'strict': '🔴 Streng (6 Emojis, 30/min, 4 Msgs/10s)'
    }
    prot_on, *_ = get_link_settings(cid)
    wl = pol.get('link_whitelist') or []
    bl = pol.get('domain_blacklist') or []
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
        f"✅ Whitelist: {len(wl)} Domains\n"
        f"❌ Blacklist: {len(bl)} Domains"
    )
    kb = [
        [InlineKeyboardButton("📊 Level ändern", callback_data=f"{cid}_spam_lvl_cycle")],
        [InlineKeyboardButton("📝 Whitelist", callback_data=f"{cid}_spam_wl_edit"),
         InlineKeyboardButton("❌ Blacklist", callback_data=f"{cid}_spam_bl_edit")],
        [InlineKeyboardButton(f"{'✅' if prot_on else '☐'} 🔗 Nur Admin-Links (Gruppe)",
                              callback_data=f"{cid}_spam_link_admins_global")],
        [InlineKeyboardButton("🎯 Topic-Regeln", callback_data=f"{cid}_spam_tsel")],
        [InlineKeyboardButton("❓ Hilfe", callback_data=f"{cid}_spam_help")],
        [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
    ]
    return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def _render_spam_topic(query, cid, topic_id):
    pol = get_spam_policy_topic(cid, topic_id) or {}
    level = pol.get('level', 'off')
    emsg = pol.get('emoji_max_per_msg', 0) or 0
    rate = pol.get('max_msgs_per_10s', 0) or 0
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
        [InlineKeyboardButton("🔗 Nur Admin-Links: TOGGLE", callback_data=f"{cid}_spam_link_admins_{topic_id}"),
         InlineKeyboardButton("✏️ Warntext", callback_data=f"{cid}_spam_link_warn_{topic_id}")],
        [InlineKeyboardButton("Whitelist bearbeiten", callback_data=f"{cid}_spam_wl_edit_{topic_id}"),
         InlineKeyboardButton("Blacklist bearbeiten", callback_data=f"{cid}_spam_bl_edit_{topic_id}")],
        [InlineKeyboardButton("Limit/Tag setzen", callback_data=f"{cid}_spam_limt_edit_{topic_id}")],
        [InlineKeyboardButton("Benachrichtigung ⏭", callback_data=f"{cid}_spam_qmode_{topic_id}")],
        [InlineKeyboardButton("↩️ Zurück (Topics)", callback_data=f"{cid}_spam_tsel")]
    ]
    return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def _render_aimod_root(query, cid):
    pol = effective_ai_mod_policy(cid, 0)
    text = (
        "🛡️ <b>KI-Moderation (global)</b>\n\n"
        f"Status: <b>{'AN' if pol['enabled'] else 'AUS'}</b> • Shadow: <b>{'AN' if pol['shadow_mode'] else 'AUS'}</b>\n"
        f"Aktionsfolge: <b>{pol['action_primary']}</b> → Eskalation nach {pol['escalate_after']} → <b>{pol['escalate_action']}</b>\n"
        f"Mute-Dauer: <b>{pol['mute_minutes']} min</b>\n"
        f"Ratenlimit: <b>{pol['max_calls_per_min']}/min</b> • Cooldown: <b>{pol['cooldown_s']}s</b>\n\n"
        f"Schwellen (0..1): tox={pol['tox_thresh']} hate={pol['hate_thresh']} sex={pol['sex_thresh']} "
        f"harass={pol['harass_thresh']} self={pol['selfharm_thresh']} viol={pol['violence_thresh']} link={pol['link_risk_thresh']}\n"
    )
    kb = [
        [InlineKeyboardButton("Ein/Aus", callback_data=f"{cid}_aimod_toggle"),
         InlineKeyboardButton("Shadow", callback_data=f"{cid}_aimod_shadow")],
        [InlineKeyboardButton("⚖️ Strikes", callback_data=f"{cid}_aimod_strikes"),
         InlineKeyboardButton("Aktion ⏭", callback_data=f"{cid}_aimod_act")],
        [InlineKeyboardButton("Eskalation ⏭", callback_data=f"{cid}_aimod_escal"),
         InlineKeyboardButton("Mute ⌛", callback_data=f"{cid}_aimod_mute_minutes")],
        [InlineKeyboardButton("Rate/Cooldown", callback_data=f"{cid}_aimod_rate"),
         InlineKeyboardButton("Schwellen", callback_data=f"{cid}_aimod_thr")],
        [InlineKeyboardButton("Warntext", callback_data=f"{cid}_aimod_warn"),
         InlineKeyboardButton("Appeal-URL", callback_data=f"{cid}_aimod_appeal")],
        [InlineKeyboardButton("Topic-Overrides", callback_data=f"{cid}_aimod_topics")],
        [InlineKeyboardButton("📄 Rohwerte (global)", callback_data=f"{cid}_aimod_raw")],
        [InlineKeyboardButton("↩️ Zurück", callback_data=f"{cid}_ai")]
    ]
    return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def _render_aimod_topic(query, cid, tid):
    pol = effective_ai_mod_policy(cid, tid)
    kb = [
        [InlineKeyboardButton("Ein/Aus", callback_data=f"{cid}_aimod_tgl_{tid}"),
         InlineKeyboardButton("Shadow", callback_data=f"{cid}_aimod_shd_{tid}")],
        [InlineKeyboardButton("Aktion ⏭", callback_data=f"{cid}_aimod_act_{tid}"),
         InlineKeyboardButton("Eskalation ⏭", callback_data=f"{cid}_aimod_esc_{tid}")],
        [InlineKeyboardButton("Schwellen", callback_data=f"{cid}_aimod_thr_{tid}")],
        [InlineKeyboardButton("Warntext", callback_data=f"{cid}_aimod_wr_{tid}"),
         InlineKeyboardButton("Appeal-URL", callback_data=f"{cid}_aimod_ap_{tid}")],
        [InlineKeyboardButton("📄 Rohwerte (Topic)", callback_data=f"{cid}_aimod_raw_{tid}")],
        [InlineKeyboardButton("↩️ Zurück (Topics)", callback_data=f"{cid}_aimod_topics")]
    ]
    txt = (
        f"🛡️ <b>Topic {tid} – KI-Moderation</b>\n"
        f"Status: <b>{'AN' if pol['enabled'] else 'AUS'}</b> • Shadow: <b>{'AN' if pol['shadow_mode'] else 'AUS'}</b>\n"
        f"Aktionsfolge: <b>{pol['action_primary']}</b> → {pol['escalate_after']} → <b>{pol['escalate_action']}</b>\n"
        f"Schwellen: tox={pol['tox_thresh']} hate={pol['hate_thresh']} sex={pol['sex_thresh']} ..."
    )
    return await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

# =========================
# Haupt-Callback-Controller
# =========================

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = (query.data or "").strip()

    # A) GRUPPENAUSWAHL (muss vor Regex passieren)
    if data == "group_select":
        groups = await get_visible_groups(update.effective_user.id)
        if not groups:
            return await query.edit_message_text("⚠️ Keine Gruppen verfügbar.")
        kb = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid, title in groups]
        return await query.edit_message_text("Wähle eine Gruppe:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("group_"):
        id_str = data.split("_", 1)[1].strip()
        if id_str.lstrip("-").isdigit():
            cid = int(id_str)
            # >>> WICHTIG: Auswahl persistieren
            context.user_data["selected_chat_id"] = cid
            # Optional: kleine Quittung vermeiden -> direkt Menü zeichnen
            return await show_group_menu(query=query, cid=cid, context=context)
        else:
            return await query.answer("Ungültige Gruppen-ID.", show_alert=True)

    # B) Danach Regex matchen
    m = re.match(r'^(-?\d+)_([a-zA-Z0-9]+)(?:_(.+))?$', data)
    
    # 1) Einheitliches Pattern: {cid}_{func}[_sub]
    if not m:
        # Versuche zuerst die gemerkte Auswahl
        cid_saved = context.user_data.get("selected_chat_id")
        if cid_saved:
            return await show_group_menu(query=query, cid=cid_saved, context=context)

        # Als Notnagel: Wenn die Nachricht in einer Gruppe gedrückt wurde,
        # nutze deren Chat-ID (verhindert "Keine Gruppe ausgewählt" in Gruppen)
        msg_chat_id = query.message.chat.id
        if str(msg_chat_id).startswith("-100"):  # Supergroup/Channel IDs
            context.user_data["selected_chat_id"] = msg_chat_id
            return await show_group_menu(query=query, cid=msg_chat_id, context=context)

        # Sonst klare Fehlermeldung
        return await query.edit_message_text("⚠️ Keine Gruppe ausgewählt.")

    cid  = int(m.group(1))
    func = m.group(2)
    sub  = m.group(3) if m.group(3) is not None else None
    lang = get_group_language(cid) or "de"
    back = InlineKeyboardMarkup([[InlineKeyboardButton(tr("↩️ Zurück", lang), callback_data=f"group_{cid}")]])

    # =========================
    # 2) Sub-Menüs (Einstiege)
    # =========================

    
    if func in ('welcome', 'rules', 'farewell') and sub is None:
        kb = [
            [InlineKeyboardButton(tr('Bearbeiten', lang), callback_data=f"{cid}_{func}_edit"),
             InlineKeyboardButton(tr('Anzeigen', lang), callback_data=f"{cid}_{func}_show")],
            [InlineKeyboardButton(tr('Löschen', lang), callback_data=f"{cid}_{func}_delete")],
            [InlineKeyboardButton(tr('⬅ Hauptmenü', lang), callback_data=f"group_{cid}")]
        ]
        text = tr(f"⚙️ {func.capitalize()} verwalten:", lang)
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    if func == 'rss' and sub is None:
        ai_faq, ai_rss = get_ai_settings(cid)
        topic_id = get_rss_topic(cid)
        topic_line = f"Aktuelles RSS-Topic: {topic_id}" if topic_id else "Kein RSS-Topic gesetzt."
        text = "📰 <b>RSS-Feeds</b>\n" + topic_line
        kb = [
            [InlineKeyboardButton(tr('Auflisten', lang), callback_data=f"{cid}_rss_list"),
             InlineKeyboardButton(tr('Feed hinzufügen', lang), callback_data=f"{cid}_rss_setrss")],
            [InlineKeyboardButton(tr('Stoppen', lang), callback_data=f"{cid}_rss_stop")],
            [InlineKeyboardButton(f"{'✅' if ai_rss else '☐'} KI-Zusammenfassung", callback_data=f"{cid}_rss_ai_toggle")],
            [InlineKeyboardButton(tr('⬅ Hauptmenü', lang), callback_data=f"group_{cid}")]
        ]
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    if func == 'captcha' and sub is None:
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

    if func == 'faq' and sub is None:
        faqs = list_faqs(cid) or []
        lines = [f"• <code>{t}</code> → {a[:30]}..." for t, a in faqs[:10]]
        ai_faq, _ = get_ai_settings(cid)
        help_text = (
            "❓ <b>FAQ-System</b>\n\n"
            "📝 <b>Hinzufügen:</b> <code>Trigger ⟶ Antwort</code>\n"
            "Beispiel: <code>hilfe ⟶ Für Unterstützung schreibe @admin</code>\n\n"
            "🔍 <b>Auslösung:</b> Wenn Nutzer 'hilfe' schreibt oder fragt\n\n"
            "🤖 <b>KI-Fallback:</b> Bei unbekannten Fragen automatische Antworten\n\n"
            "<b>Aktuelle FAQs:</b>\n" + ("\n".join(lines) if lines else "Noch keine Einträge.")
        )
        kb = [
            [InlineKeyboardButton("➕ FAQ hinzufügen", callback_data=f"{cid}_faq_add"),
             InlineKeyboardButton("🗑 FAQ löschen", callback_data=f"{cid}_faq_del")],
            [InlineKeyboardButton(f"{'✅' if ai_faq else '☐'} KI-Fallback", callback_data=f"{cid}_faq_ai_toggle")],
            [InlineKeyboardButton("❓ Hilfe", callback_data=f"{cid}_faq_help")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
        ]
        return await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    # FAQ – Aktionen
    if func == 'faq' and sub:
        if sub == 'ai_toggle':
            ai_faq, _ = get_ai_settings(cid)
            set_ai_settings(cid, faq=not ai_faq)
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)

            # Menü erneut rendern (kein Rücksprung in denselben Zweig!)
            faqs = list_faqs(cid) or []
            lines = [f"• <code>{t}</code> → {a[:30]}..." for t, a in faqs[:10]]
            ai_faq2, _ = get_ai_settings(cid)
            help_text = (
                "❓ <b>FAQ-System</b>\n\n"
                "📝 <b>Hinzufügen:</b> <code>Trigger ⟶ Antwort</code>\n"
                "Beispiel: <code>hilfe ⟶ Für Unterstützung schreibe @admin</code>\n\n"
                "🔍 <b>Auslösung:</b> Wenn Nutzer 'hilfe' schreibt oder fragt\n\n"
                "🤖 <b>KI-Fallback:</b> Bei unbekannten Fragen automatische Antworten\n\n"
                "<b>Aktuelle FAQs:</b>\n" + ("\n".join(lines) if lines else "Noch keine Einträge.")
            )
            kb = [
                [InlineKeyboardButton("➕ FAQ hinzufügen", callback_data=f"{cid}_faq_add"),
                InlineKeyboardButton("🗑 FAQ löschen", callback_data=f"{cid}_faq_del")],
                [InlineKeyboardButton(f"{'✅' if ai_faq2 else '☐'} KI-Fallback", callback_data=f"{cid}_faq_ai_toggle")],
                [InlineKeyboardButton("❓ Hilfe", callback_data=f"{cid}_faq_help")],
                [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
            ]
            return await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    
    if func == 'language' and sub is None:
        cur = get_lang(cid) or 'de'
        kb = [[InlineKeyboardButton(f"{'✅ ' if c == cur else ''}{n}", callback_data=f"{cid}_setlang_{c}")]
              for c, n in LANGUAGES.items()]
        kb.append([InlineKeyboardButton('↩️ Zurück', callback_data=f'group_{cid}')])
        return await query.edit_message_text(tr('🌐 Wähle Sprache:', cur), reply_markup=InlineKeyboardMarkup(kb))

    if func == 'night' and sub is None:
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

    if func == 'mood' and sub is None:
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

    if func == 'ai' and sub is None:
        ai_faq, ai_rss = get_ai_settings(cid)
        text = (
            "🤖 <b>KI-Einstellungen</b>\n\n"
            "🎯 <b>Verfügbare Features:</b>\n"
            f"• FAQ-Fallback: {'✅' if ai_faq else '❌'}\n"
            f"• RSS-Zusammenfassung: {'✅' if ai_rss else '❌'}\n\n"
            "🛡️ <b>Moderation</b>: Feineinstellungen je Topic möglich"
        )
        kb = [
            [InlineKeyboardButton(f"{'✅' if ai_faq else '☐'} FAQ-Fallback", callback_data=f"{cid}_ai_faq_toggle")],
            [InlineKeyboardButton(f"{'✅' if ai_rss else '☐'} RSS-Zusammenfassung", callback_data=f"{cid}_ai_rss_toggle")],
            [InlineKeyboardButton("🛡️ Moderation", callback_data=f"{cid}_aimod")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
        ]
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    # KI – Aktionen
    if func == 'ai' and sub:
        if sub == 'faq_toggle':
            ai_faq, ai_rss = get_ai_settings(cid)
            set_ai_settings(cid, faq=not ai_faq)
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
            # zurück ins KI-Hauptmenü (anderes data, keine Schleife)
            query.data = f"{cid}_ai"
            return await menu_callback(update, context)

        if sub == 'rss_toggle':
            ai_faq, ai_rss = get_ai_settings(cid)
            set_ai_settings(cid, rss=not ai_rss)
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
            query.data = f"{cid}_ai"
            return await menu_callback(update, context)
        
        if func == 'aimod' and sub is None:
            pol = effective_ai_mod_policy(cid, 0)
            text = (
                "🛡️ <b>KI-Moderation (global)</b>\n\n"
                f"Status: <b>{'AN' if pol['enabled'] else 'AUS'}</b> • Shadow: <b>{'AN' if pol['shadow_mode'] else 'AUS'}</b>\n"
                f"Aktionsfolge: <b>{pol['action_primary']}</b> → Eskalation nach {pol['escalate_after']} → <b>{pol['escalate_action']}</b>\n"
                f"Mute-Dauer: <b>{pol['mute_minutes']} min</b>\n"
                f"Ratenlimit: <b>{pol['max_calls_per_min']}/min</b> • Cooldown: <b>{pol['cooldown_s']}s</b>\n\n"
                f"Schwellen (0..1): tox={pol['tox_thresh']} hate={pol['hate_thresh']} sex={pol['sex_thresh']} "
                f"harass={pol['harass_thresh']} self={pol['selfharm_thresh']} viol={pol['violence_thresh']} link={pol['link_risk_thresh']}\n"
            )
            kb = [
                [InlineKeyboardButton("Ein/Aus", callback_data=f"{cid}_aimod_toggle"),
                InlineKeyboardButton("Shadow", callback_data=f"{cid}_aimod_shadow")],
                [InlineKeyboardButton("⚖️ Strikes", callback_data=f"{cid}_aimod_strikes"),
                InlineKeyboardButton("Aktion ⏭", callback_data=f"{cid}_aimod_act")],
                [InlineKeyboardButton("Eskalation ⏭", callback_data=f"{cid}_aimod_escal"),
                InlineKeyboardButton("Mute ⌛", callback_data=f"{cid}_aimod_mute_minutes")],
                [InlineKeyboardButton("Rate/Cooldown", callback_data=f"{cid}_aimod_rate"),
                InlineKeyboardButton("Schwellen", callback_data=f"{cid}_aimod_thr")],
                [InlineKeyboardButton("Warntext", callback_data=f"{cid}_aimod_warn"),
                InlineKeyboardButton("Appeal-URL", callback_data=f"{cid}_aimod_appeal")],
                [InlineKeyboardButton("Topic-Overrides", callback_data=f"{cid}_aimod_topics")],
                [InlineKeyboardButton("📄 Rohwerte (global)", callback_data=f"{cid}_aimod_raw")],  # <-- NEU
                [InlineKeyboardButton("↩️ Zurück", callback_data=f"{cid}_ai")]
            ]
            return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    # =========================
    # 3) Aktionen / Unterpunkte
    # =========================

    # --- Welcome / Rules / Farewell ---
    if func in ('welcome', 'rules', 'farewell') and sub:
        get_map = {'welcome': get_welcome, 'rules': get_rules, 'farewell': get_farewell}
        set_map = {'welcome': set_welcome, 'rules': set_rules, 'farewell': set_farewell}
        del_map = {'welcome': delete_welcome, 'rules': delete_rules, 'farewell': delete_farewell}

        if sub == 'show':
            rec = get_map[func](cid)
            if rec:
                text = rec[1] if len(rec) > 1 else ''
                media = rec[0] if len(rec) > 0 else None
                if media:
                    try:
                        await query.message.reply_photo(photo=media, caption=text, reply_markup=back, parse_mode="HTML")
                    except Exception:
                        try:
                            await query.message.reply_document(document=media, caption=text, reply_markup=back, parse_mode="HTML")
                        except Exception as e2:
                            await query.edit_message_text(f"{text}\n\n⚠️ Medien konnten nicht geladen werden.", reply_markup=back, parse_mode="HTML")
                else:
                    await query.edit_message_text(text, reply_markup=back, parse_mode="HTML")
            else:
                await query.edit_message_text(f"Keine {func}-Nachricht gesetzt.", reply_markup=back)
            return

        if sub == 'delete':
            del_map[func](cid)
            await query.answer(tr(f"✅ {func.capitalize()} gelöscht.", lang), show_alert=True)
            return await query.edit_message_text(tr(f"{func.capitalize()} entfernt.", lang), reply_markup=back)

        if sub == 'edit':
            context.user_data['last_edit'] = (cid, func)
            label = {'welcome': 'Begrüßung', 'rules': 'Regeln', 'farewell': 'Abschied'}[func]
            set_pending_input(query.message.chat.id, update.effective_user.id, "edit",
                              {"target_chat_id": cid, "what": func})
            return await query.message.reply_text(f"✏️ Sende nun die neue {label}:", reply_markup=ForceReply(selective=True))

    # --- Captcha ---
    if func == 'captcha' and sub:
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

    # --- RSS ---
    if func == 'rss' and sub:
        if sub == 'setrss':
            if not get_rss_topic(cid):
                await query.answer('❗ Kein RSS-Topic gesetzt. Bitte erst /settopicrss ausführen.', show_alert=True)
                return await show_group_menu(query=query, cid=cid, context=context)
            context.user_data.pop('awaiting_mood_question', None)
            context.user_data.pop('last_edit', None)
            context.user_data.update(awaiting_rss_url=True, rss_group_id=cid)
            set_pending_input(query.message.chat.id, update.effective_user.id, "rss_url", {"target_chat_id": cid})
            await query.message.reply_text('📰 Bitte sende die RSS-URL:', reply_markup=ForceReply(selective=True))
            await query.answer("Sende nun die RSS-URL als Antwort.")
            return

        if sub == 'list':
            feeds = db_list_rss_feeds(cid) or []
            if not feeds:
                return await query.edit_message_text('Keine RSS-Feeds.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('↩️ Zurück', callback_data=f'group_{cid}')]]))
            rows = []
            text_lines = ["Aktive Feeds:"]
            for item in feeds:
                # erwartetes Format: (url, topic_id, …)
                url = item[0]
                tid = item[1] if len(item) > 1 else "?"
                opts = get_rss_feed_options(cid, url) or {}
                img_on = bool(opts.get("post_images", False))
                text_lines.append(f"- {url} (Topic {tid})")
                rows.append([
                    InlineKeyboardButton(f"🖼 Bilder: {'AN' if img_on else 'AUS'}", callback_data=f"{cid}_rss_img_toggle|{url}"),
                    InlineKeyboardButton("🗑 Entfernen", callback_data=f"{cid}_rss_del|{url}")
                ])
            rows.append([InlineKeyboardButton('↩️ Zurück', callback_data=f'group_{cid}')])
            return await query.edit_message_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(rows))

        if sub == 'stop':
            # stoppt alle Feeds der Gruppe
            remove_rss_feed(cid)
            await query.answer('✅ RSS gestoppt', show_alert=True)
            return await _render_rss_list(query, cid, lang)

        if sub == 'ai_toggle':
            ai_faq, ai_rss = get_ai_settings(cid)
            set_ai_settings(cid, rss=not ai_rss)
            log_feature_interaction(cid, update.effective_user.id, "menu:rss", {"action": "ai_toggle", "from": ai_rss, "to": (not ai_rss)})
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
            query.data = f"{cid}_rss"
            return await _render_rss_list(query, cid, lang)

        # Bild-Posting pro URL togglen
        if data.startswith(f"{cid}_rss_img_toggle|"):
            url = data.split("|", 1)[1]
            try:
                from database import get_rss_feed_options as _get_opts, set_rss_feed_options as _set_opts
                cur = _get_opts(cid, url) or {}
                new_val = not bool(cur.get("post_images", False))
                _set_opts(cid, url, post_images=new_val)
                log_feature_interaction(cid, update.effective_user.id, "menu:rss", {"action": "post_images_toggle", "url": url, "value": new_val})
                await query.answer(f"🖼 Bilder: {'AN' if new_val else 'AUS'}", show_alert=True)
            except Exception as e:
                await query.answer(f"⚠️ Konnte post_images nicht togglen: {e}", show_alert=True)
            query.data = f"{cid}_rss_list"
            return await _render_rss_list(query, cid, lang)

        if data.startswith(f"{cid}_rss_del|"):
            url = data.split("|", 1)[1]
            try:
                # Einzelnen Feed entfernen; falls deine DB-Funktion anders heißt → anpassen
                from database import remove_single_rss_feed
                remove_single_rss_feed(cid, url)
                await query.answer("🗑 Feed entfernt.", show_alert=True)
            except Exception:
                await query.answer("⚠️ Entfernen fehlgeschlagen (prüfe DB-Funktion).", show_alert=True)
            query.data = f"{cid}_rss_list"
            return await _render_rss_list(query, cid, lang)

    # --- Spam ---
    if func == 'spam' and sub is None:
        pol = get_spam_policy_topic(cid, 0) or {}
        level = pol.get('level', 'off')
        prot_on, *_ = get_link_settings(cid)
        wl = pol.get('link_whitelist') or []
        bl = pol.get('domain_blacklist') or []
        wl_txt = ", ".join(wl) if wl else "–"
        bl_txt = ", ".join(bl) if bl else "–"
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
            f"✅ Whitelist: {len(wl)} Domains\n"
            f"❌ Blacklist: {len(bl)} Domains"
        )
        kb = [
            [InlineKeyboardButton("📊 Level ändern", callback_data=f"{cid}_spam_lvl_cycle")],
            [InlineKeyboardButton("📝 Whitelist", callback_data=f"{cid}_spam_wl_edit"),
             InlineKeyboardButton("❌ Blacklist", callback_data=f"{cid}_spam_bl_edit")],
            [InlineKeyboardButton(f"{'✅' if prot_on else '☐'} 🔗 Nur Admin-Links (Gruppe)",
                                  callback_data=f"{cid}_spam_link_admins_global")],
            [InlineKeyboardButton("🎯 Topic-Regeln", callback_data=f"{cid}_spam_tsel")],
            [InlineKeyboardButton("❓ Hilfe", callback_data=f"{cid}_spam_help")],
            [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
        ]
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    if func == 'spam' and sub:
        if sub == 'lvl_cycle':
            pol = get_spam_policy_topic(cid, 0) or {'level': 'off'}
            order = ['off', 'light', 'medium', 'strict']
            nxt = order[(order.index(pol.get('level', 'off')) + 1) % len(order)]
            set_spam_policy_topic(cid, 0, level=nxt)
            await query.answer(f"Level: {nxt}", show_alert=True)
            return await _render_spam_root(query, cid)

        if sub == 'tsel':
            return await query.edit_message_text(
                "🧹 <b>Spamfilter: Topic wählen</b>",
                reply_markup=_topics_keyboard(cid, page=0, cb_prefix=f"{cid}_spam_t_"),
                parse_mode="HTML"
            )

        if sub.startswith('tpnav_'):
            # Navigations-Callbacks der Topic-Auswahl
            # Format: f"{cid}_tpnav_{cb_prefix}{page}"
            payload = sub.split('tpnav_', 1)[1]
            # payload beginnt mit cb_prefix, Ende ist page
            # cb_prefix hier wieder zusammenbauen:
            # Wir erkennen an unserem Anwendungsfall nur Spam-Topic-Auswahl:
            # → cb_prefix = f"{cid}_spam_t_"
            page = int(payload.replace(f"{cid}_spam_t_", ""))
            return await query.edit_message_reply_markup(reply_markup=_topics_keyboard(cid, page, cb_prefix=f"{cid}_spam_t_"))

        if sub.startswith('t_'):
            topic_id = int(sub.split('_', 1)[1])
            pol = get_spam_policy_topic(cid, topic_id) or {}
            level = pol.get('level', 'off')
            emsg = pol.get('emoji_max_per_msg', 0) or 0
            rate = pol.get('max_msgs_per_10s', 0) or 0
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
                [InlineKeyboardButton("🔗 Nur Admin-Links: TOGGLE", callback_data=f"{cid}_spam_link_admins_{topic_id}"),
                 InlineKeyboardButton("✏️ Warntext", callback_data=f"{cid}_spam_link_warn_{topic_id}")],
                [InlineKeyboardButton("Whitelist bearbeiten", callback_data=f"{cid}_spam_wl_edit_{topic_id}"),
                 InlineKeyboardButton("Blacklist bearbeiten", callback_data=f"{cid}_spam_bl_edit_{topic_id}")],
                [InlineKeyboardButton("Limit/Tag setzen", callback_data=f"{cid}_spam_limt_edit_{topic_id}")],
                [InlineKeyboardButton("Benachrichtigung ⏭", callback_data=f"{cid}_spam_qmode_{topic_id}")],
                [InlineKeyboardButton("↩️ Zurück (Topics)", callback_data=f"{cid}_spam_tsel")]
            ]
            return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

        if sub == 'link_admins_global':
            # Toggle globale Link-Admin-Only
            cur, warn_text, *_ = get_link_settings(cid)
            await _call_db_safe(set_link_settings, cid, only_admin_links=not cur, warning_text=warn_text)
            await query.answer(f"Nur Admin-Links: {'AN' if not cur else 'AUS'}", show_alert=True)
            return await _render_spam_root(query, cid)

        if sub.startswith('link_admins_'):
            tid = int(sub.split('_')[-1])
            pol = get_spam_policy_topic(cid, tid) or {}
            cur = bool(pol.get('only_admin_links', False))
            set_spam_policy_topic(cid, tid, only_admin_links=not cur)
            await query.answer(f"Nur Admin-Links (Topic {tid}): {'AN' if not cur else 'AUS'}", show_alert=True)
            update.callback_query.data = f"{cid}_spam_t_{tid}"
            return await _render_spam_topic(query, cid, topic_id)

        if sub.startswith('link_warn_'):
            tid = int(sub.split('_')[-1])
            context.user_data['awaiting_link_warn'] = True
            context.user_data['link_warn_group'] = cid
            set_pending_input(query.message.chat.id, update.effective_user.id, "link_warn", {"chat_id": cid, "topic_id": tid})
            await query.message.reply_text("Bitte neuen Warntext senden:", reply_markup=ForceReply(selective=True))
            return

        if sub.startswith('setlvl_'):
            topic_id = int(sub.split('_', 1)[1])
            pol = get_spam_policy_topic(cid, topic_id) or {'level': 'off'}
            order = ['off', 'light', 'medium', 'strict']
            nxt = order[(order.index(pol.get('level', 'off')) + 1) % len(order)]
            set_spam_policy_topic(cid, topic_id, level=nxt)
            await query.answer(f"Level: {nxt}", show_alert=True)
            update.callback_query.data = f"{cid}_spam_t_{topic_id}"
            return await _render_spam_topic(query, cid, topic_id)

        if sub.startswith(('emj_', 'rate_', 'wl_edit_', 'bl_edit_', 'limt_edit_', 'qmode_')):
            parts = sub.split('_')
            if parts[0] == 'emj':
                op, topic_id = parts[1], int(parts[2])
                pol = get_spam_policy_topic(cid, topic_id) or {}
                cur = (pol.get('emoji_max_per_msg') or 0) + (1 if op == '+' else -1)
                set_spam_policy_topic(cid, topic_id, emoji_max_per_msg=max(0, cur))
                update.callback_query.data = f"{cid}_spam_t_{topic_id}"
                return await _render_spam_topic(query, cid, topic_id)

            if parts[0] == 'rate':
                op, topic_id = parts[1], int(parts[2])
                pol = get_spam_policy_topic(cid, topic_id) or {}
                cur = (pol.get('max_msgs_per_10s') or 0) + (1 if op == '+' else -1)
                set_spam_policy_topic(cid, topic_id, max_msgs_per_10s=max(0, cur))
                update.callback_query.data = f"{cid}_spam_t_{topic_id}"
                return await _render_spam_topic(query, cid, topic_id)

            if parts[0] in ('wl', 'bl') and parts[1] == 'edit':
                topic_id = int(parts[2])
                which = 'whitelist' if parts[0] == 'wl' else 'blacklist'
                context.user_data.update(
                    awaiting_spam_whitelist=(which == 'whitelist'),
                    awaiting_spam_blacklist=(which == 'blacklist'),
                    spam_group_id=cid,
                    spam_topic_id=topic_id
                )
                set_pending_input(
                    query.message.chat.id,
                    update.effective_user.id,
                    "spam_edit",
                    {"chat_id": cid, "topic_id": topic_id, "which": which}
                )
                prompt = "Sende die Whitelist-Domains, Komma-getrennt:" if which == 'whitelist' else "Sende die Blacklist-Domains, Komma-getrennt:"
                return await query.message.reply_text(prompt, reply_markup=ForceReply(selective=True))

            if parts[0] == 'limt' and parts[1] == 'edit':
                topic_id = int(parts[2])
                context.user_data.update(awaiting_topic_limit=True, spam_group_id=cid, spam_topic_id=topic_id)
                set_pending_input(query.message.chat.id, update.effective_user.id, "spam_edit",
                                  {"chat_id": cid, "topic_id": topic_id, "which": "limit"})
                return await query.message.reply_text("Bitte Limit/Tag/User als Zahl senden (0 = aus):", reply_markup=ForceReply(selective=True))

            if parts[0] == 'qmode':
                topic_id = int(parts[2])
                pol = get_spam_policy_topic(cid, topic_id) or {'quota_notify': 'smart'}
                order = ['off', 'smart', 'always']
                cur = (pol.get('quota_notify') or 'smart').lower()
                nxt = order[(order.index(cur) + 1) % len(order)]
                set_spam_policy_topic(cid, topic_id, quota_notify=nxt)
                await query.answer(f"Rest-Info: {nxt}", show_alert=True)
                update.callback_query.data = f"{cid}_spam_t_{topic_id}"
                return await _render_spam_topic(query, cid, topic_id)

    # --- Topic-Router ---
    if func == 'router' and sub is None:
        rules = list_topic_router_rules(cid) or []
        lines = [f"#{rid} → topic {tgt} | {'ON' if en else 'OFF'} | del={do} warn={wn} | kw={kws or []} dom={doms or []}"
                 for (rid, tgt, en, do, wn, kws, doms) in rules]
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

    if func == 'router' and sub:
        if sub in ('tsel_kw', 'tsel_dom'):
            cb_prefix = f"{cid}_router_pick_kw_" if sub.endswith('kw') else f"{cid}_router_pick_dom_"
            return await query.edit_message_text(
                "🧭 <b>Router: Ziel-Topic wählen</b>",
                reply_markup=_topics_keyboard(cid, page=0, cb_prefix=cb_prefix),
                parse_mode="HTML"
            )
        if sub.startswith('pick_kw_'):
            topic_id = int(sub.split('_')[-1])
            context.user_data.update(awaiting_router_add_keywords=True, router_group_id=cid, router_target_tid=topic_id)
            set_pending_input(query.message.chat.id, update.effective_user.id, "router_add_kw",
                              {"chat_id": cid, "target_tid": topic_id})
            return await query.message.reply_text("Sende Keywords (Komma-getrennt) für die Regel:", reply_markup=ForceReply(selective=True))
        if sub.startswith('pick_dom_'):
            topic_id = int(sub.split('_')[-1])
            context.user_data.update(awaiting_router_add_domains=True, router_group_id=cid, router_target_tid=topic_id)
            set_pending_input(query.message.chat.id, update.effective_user.id, "router_add_dom",
                              {"chat_id": cid, "target_tid": topic_id})
            return await query.message.reply_text("Sende Domains (Komma-getrennt) für die Regel:", reply_markup=ForceReply(selective=True))
        if sub == 'del':
            context.user_data.update(awaiting_router_delete=True, router_group_id=cid)
            set_pending_input(query.message.chat.id, update.effective_user.id, "router_delete", {"chat_id": cid})
            return await query.message.reply_text("Gib die Regel-ID an, die gelöscht werden soll:", reply_markup=ForceReply(selective=True))
        if sub == 'toggle':
            context.user_data.update(awaiting_router_toggle=True, router_group_id=cid)
            set_pending_input(query.message.chat.id, update.effective_user.id, "router_toggle", {"chat_id": cid})
            return await query.message.reply_text("Format: <regel_id> on|off", reply_markup=ForceReply(selective=True))

    # --- AI Moderation: globale Detailaktionen ---
    if func == 'aimod' and sub:
        if sub == 'toggle':
            pol = effective_ai_mod_policy(cid, 0)
            set_ai_mod_settings(cid, 0, enabled=not pol['enabled'])
            query.data = f"{cid}_aimod"
            return await _render_aimod_root(query, cid)
        if sub == 'shadow':
            pol = effective_ai_mod_policy(cid, 0)
            set_ai_mod_settings(cid, 0, shadow_mode=not pol['shadow_mode'])
            return await _render_aimod_root(query, cid)
        if sub == 'act':
            order = ['delete', 'warn', 'mute', 'ban']
            cur = effective_ai_mod_policy(cid, 0)['action_primary']
            nxt = order[(order.index(cur) + 1) % len(order)]
            set_ai_mod_settings(cid, 0, action_primary=nxt)
            return await _render_aimod_root(query, cid)
        if sub == 'escal':
            order = ['mute', 'ban']
            cur = effective_ai_mod_policy(cid, 0)['escalate_action']
            nxt = order[(order.index(cur) + 1) % len(order)]
            set_ai_mod_settings(cid, 0, escalate_action=nxt)
            return await _render_aimod_root(query, cid)
        if sub == 'thr':
            context.user_data.update(awaiting_aimod_thresholds=True, aimod_chat_id=cid, aimod_topic_id=0)
            return await query.message.reply_text(
                "Schwellen senden (JSON, z.B.):\n"
                '{"tox":0.9,"hate":0.85,"sex":0.9,"harass":0.9,"self":0.95,"viol":0.9,"link":0.95}'
            )
        if sub == 'warn':
            context.user_data.update(awaiting_aimod_warn=True, aimod_chat_id=cid, aimod_topic_id=0)
            return await query.message.reply_text("Neuen Warn-Text senden:", reply_markup=ForceReply(selective=True))
        if sub == 'appeal':
            context.user_data.update(awaiting_aimod_appeal=True, aimod_chat_id=cid, aimod_topic_id=0)
            return await query.message.reply_text("Appeal-URL senden (leer = entfernen):", reply_markup=ForceReply(selective=True))
        if sub == 'rate':
            context.user_data.update(awaiting_aimod_rate=True, aimod_chat_id=cid, aimod_topic_id=0)
            return await query.message.reply_text("Format: max_per_min=20 cooldown_s=30 mute_minutes=60", reply_markup=ForceReply(selective=True))
        if sub == 'strikes':
            pol = effective_ai_mod_policy(cid, 0)
            top = top_strike_users(cid, 10)
            lines = [f"• <code>{uid}</code>: {pts} Pkt" for uid, pts in (top or [])] or ["(keine)"]
            txt = (
                "⚖️ <b>Strike-System</b>\n"
                f"Mute ab: <b>{pol['strike_mute_threshold']}</b> • Ban ab: <b>{pol['strike_ban_threshold']}</b>\n"
                f"Decay: <b>{pol['strike_decay_days']} Tage</b> • Punkte/Hit: <b>{pol['strike_points_per_hit']}</b>\n\n" +
                "\n".join(lines)
            )
            kb = [
                [InlineKeyboardButton("Mute-Schwelle",  callback_data=f"{cid}_aimod_strk_mute"),
                 InlineKeyboardButton("Ban-Schwelle",   callback_data=f"{cid}_aimod_strk_ban")],
                [InlineKeyboardButton("Decay (Tage)",   callback_data=f"{cid}_aimod_strk_decay"),
                 InlineKeyboardButton("Punkte/Hit",     callback_data=f"{cid}_aimod_strk_pph")],
                [InlineKeyboardButton("↩️ Zurück", callback_data=f"{cid}_aimod")]
            ]
            return await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        if sub in ('strk_mute', 'strk_ban', 'strk_decay', 'strk_pph'):
            key = {'strk_mute':'strike_mute_threshold','strk_ban':'strike_ban_threshold',
                   'strk_decay':'strike_decay_days','strk_pph':'strike_points_per_hit'}[sub]
            context.user_data.update(awaiting_aimod_strike_cfg=True, aimod_chat_id=cid, aimod_key=key)
            label = {"strike_mute_threshold":"Mute-Schwelle","strike_ban_threshold":"Ban-Schwelle",
                     "strike_decay_days":"Decay (Tage)","strike_points_per_hit":"Punkte/Hit"}[key]
            return await query.message.reply_text(f"{label} als Zahl senden:", reply_markup=ForceReply(selective=True))
        if sub == 'topics':
            # Topic-Auswahl für Overrides
            return await query.edit_message_text("Wähle Topic für Override:", reply_markup=_topics_keyboard(cid, 0, cb_prefix=f"{cid}_aimod_topic_"), parse_mode="HTML")
        if sub.startswith('tpnav_'):
            # Navigation für aimod topics
            payload = sub.split('tpnav_', 1)[1]
            page = int(payload.replace(f"{cid}_aimod_topic_", ""))
            return await query.edit_message_reply_markup(reply_markup=_topics_keyboard(cid, page, cb_prefix=f"{cid}_aimod_topic_"))
        if sub.startswith('topic_'):
            tid = int(sub.split('_', 1)[1])
            pol = effective_ai_mod_policy(cid, tid)
            kb = [
                [InlineKeyboardButton("Ein/Aus", callback_data=f"{cid}_aimod_tgl_{tid}"),
                 InlineKeyboardButton("Shadow", callback_data=f"{cid}_aimod_shd_{tid}")],
                [InlineKeyboardButton("Aktion ⏭", callback_data=f"{cid}_aimod_act_{tid}"),
                 InlineKeyboardButton("Eskalation ⏭", callback_data=f"{cid}_aimod_esc_{tid}")],
                [InlineKeyboardButton("Schwellen", callback_data=f"{cid}_aimod_thr_{tid}")],
                [InlineKeyboardButton("Warntext", callback_data=f"{cid}_aimod_wr_{tid}"),
                 InlineKeyboardButton("Appeal-URL", callback_data=f"{cid}_aimod_ap_{tid}")],
                [InlineKeyboardButton("📄 Rohwerte (Topic)", callback_data=f"{cid}_aimod_raw_{tid}")],
                [InlineKeyboardButton("↩️ Zurück (Topics)", callback_data=f"{cid}_aimod_topics")]
            ]
            txt = (
                f"🛡️ <b>Topic {tid} – KI-Moderation</b>\n"
                f"Status: <b>{'AN' if pol['enabled'] else 'AUS'}</b> • Shadow: <b>{'AN' if pol['shadow_mode'] else 'AUS'}</b>\n"
                f"Aktionsfolge: <b>{pol['action_primary']}</b> → {pol['escalate_after']} → <b>{pol['escalate_action']}</b>\n"
                f"Schwellen: tox={pol['tox_thresh']} hate={pol['hate_thresh']} sex={pol['sex_thresh']} ..."
            )
            return await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        if sub.startswith(('tgl_', 'shd_', 'act_', 'esc_', 'thr_', 'wr_', 'ap_')):
            # Topic-spezifische Settings setzen
            action, tid = sub.split('_', 1)
            tid = int(tid)
            pol = effective_ai_mod_policy(cid, tid)
            if action == 'tgl':
                set_ai_mod_settings(cid, tid, enabled=not pol['enabled'])
            elif action == 'shd':
                set_ai_mod_settings(cid, tid, shadow_mode=not pol['shadow_mode'])
            elif action == 'act':
                order = ['delete', 'warn', 'mute', 'ban']; cur = pol['action_primary']
                nxt = order[(order.index(cur) + 1) % len(order)]
                set_ai_mod_settings(cid, tid, action_primary=nxt)
            elif action == 'esc':
                order = ['mute', 'ban']; cur = pol['escalate_action']
                nxt = order[(order.index(cur) + 1) % len(order)]
                set_ai_mod_settings(cid, tid, escalate_action=nxt)
            elif action == 'thr':
                context.user_data.update(awaiting_aimod_thresholds=True, aimod_chat_id=cid, aimod_topic_id=tid)
                return await query.message.reply_text(
                    "Schwellen senden (JSON, z.B.):\n"
                    '{"tox":0.9,"hate":0.85,"sex":0.9,"harass":0.9,"self":0.95,"viol":0.9,"link":0.95}'
                )
            elif action == 'wr':
                context.user_data.update(awaiting_aimod_warn=True, aimod_chat_id=cid, aimod_topic_id=tid)
                return await query.message.reply_text("Neuen Warn-Text senden:", reply_markup=ForceReply(selective=True))
            elif action == 'ap':
                context.user_data.update(awaiting_aimod_appeal=True, aimod_chat_id=cid, aimod_topic_id=tid)
                return await query.message.reply_text("Appeal-URL senden (leer = entfernen):", reply_markup=ForceReply(selective=True))
            # zurück in Topic-Ansicht
            query.data = f"{cid}_aimod_topic_{tid}"
            return await _render_aimod_topic(query, cid, tid)

    if func == 'aimod' and sub == 'raw':
        raw = get_ai_mod_settings(cid, 0) or {}   # <-- Import wird hier genutzt
        import json as _json
        txt = "📄 <b>Rohwerte – Global</b>\n<pre>" + _json.dumps(raw, ensure_ascii=False, indent=2) + "</pre>"
        return await query.edit_message_text(txt, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Zurück", callback_data=f"{cid}_aimod")]]))

    # Topic: Rohwerte
    if func == 'aimod' and sub and sub.startswith('raw_'):
        tid = int(sub.split('_', 1)[1])
        raw = get_ai_mod_settings(cid, tid) or {}   # <-- Import wird hier genutzt
        import json as _json
        txt = f"📄 <b>Rohwerte – Topic {tid}</b>\n<pre>" + _json.dumps(raw, ensure_ascii=False, indent=2) + "</pre>"
        return await query.edit_message_text(txt, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Zurück (Topic)", callback_data=f"{cid}_aimod_topic_{tid}")]]))
    
    # --- Stats / Clean ---
    if func == 'toggle' and sub == 'stats':
        cur = is_daily_stats_enabled(cid)
        set_daily_stats(cid, not cur)
        await query.answer(tr(f"Tagesstatistik {'aktiviert' if not cur else 'deaktiviert'}", lang), show_alert=True)
        return await show_group_menu(query=query, cid=cid, context=context)

    if func == 'clean' and sub == 'delete':
        await query.answer('⏳ Bereinige…')
        try:
            removed = await clean_delete_accounts_for_chat(cid, context.bot)
            text = f"✅ {removed} Accounts entfernt."
            return await query.edit_message_text(text, reply_markup=back)
        except Exception as e:
            error_text = f"⚠️ Fehler bei der Bereinigung: {str(e)}"
            return await query.edit_message_text(error_text, reply_markup=back)

    if func == 'stats' and sub == 'export':
        return await export_stats_csv_command(update, context)

    if func == 'stats' and sub is None:
        context.user_data['stats_group_id'] = cid
        return await stats_command(update, context)

    # --- Mood ---
    if func == 'mood' and sub:
        if sub == 'show':
            q = get_mood_question(cid) or tr('Wie fühlst du dich heute?', get_group_language(cid) or 'de')
            return await query.edit_message_text(
                f"📖 {tr('Aktuelle Mood-Frage', lang)}:\n\n{q}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"{cid}_mood")]])
            )
        if sub == 'send':
            q = get_mood_question(cid) or tr('Wie fühlst du dich heute?', get_group_language(cid) or 'de')
            topic_id = get_mood_topic(cid)
            if not topic_id:
                await query.answer(tr('❗ Kein Mood-Topic gesetzt. Sende /setmoodtopic im gewünschten Thema.', lang), show_alert=True)
                # WICHTIG: zurück auf das Untermenü schalten, sonst Endlosschleife
                query.data = f"{cid}_mood"
                return await menu_callback(update, context)

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("👍", callback_data="mood_like"),
                InlineKeyboardButton("👎", callback_data="mood_dislike"),
                InlineKeyboardButton("🤔", callback_data="mood_think")]
            ])
            await context.bot.send_message(chat_id=cid, text=q, reply_markup=kb, message_thread_id=topic_id)
            await query.answer(tr('✅ Mood-Frage gesendet.', lang), show_alert=True)

            # WICHTIG: hier ebenfalls zurück ins Mood-Menü (anderes data!)
            query.data = f"{cid}_mood"
            return await menu_callback(update, context)
        
        if sub == 'topic_help':
            help_txt = (
                "🧵 <b>Topic setzen</b>\n\n"
                "1) Öffne das gewünschte Forum-Thema.\n"
                "2) Sende dort <code>/setmoodtopic</code>\n"
                f"   {tr('(oder antworte in dem Thema auf eine Nachricht und sende den Befehl)', lang)}.\n"
                "3) Fertig – zukünftige Mood-Fragen landen in diesem Thema."
            )
            return await query.edit_message_text(
                help_txt, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"{cid}_mood")]])
            )

        if sub == 'set_start':
            context.user_data['awaiting_nm_time'] = ('start', cid)
            await query.message.reply_text(tr('Bitte Startzeit im Format HH:MM senden (z. B. 22:00).', lang), reply_markup=ForceReply(selective=True))
            return
        if sub == 'set_end':
            context.user_data['awaiting_nm_time'] = ('end', cid)
            await query.message.reply_text(tr('Bitte Endzeit im Format HH:MM senden (z. B. 06:00).', lang), reply_markup=ForceReply(selective=True))
            return

    if func == 'setlang' and sub:
        lang_code = sub
        set_group_language(cid, lang_code)   # <-- Import wird hier bewusst genutzt
        await query.answer(
            tr(f"Gruppensprache gesetzt: {LANGUAGES.get(lang_code, lang_code)}", lang_code),
            show_alert=True
        )
        # Menü neu zeichnen in neuer Sprache
        return await show_group_menu(query=query, cid=cid, context=context)
    
    # --- Hilfe (Handbuch) / Patchnotes: sauber getrennt, CID-gebunden ---
    m_help = re.match(r'^(-?\d+)_help$', data)
    m_notes = re.match(r'^(-?\d+)_patchnotes$', data)

    if m_help:
        cid = int(m_help.group(1))
        lang = get_group_language(cid) or "de"
        translated = translate_hybrid(HELP_TEXT, target_lang=lang)
        path = f'/tmp/user_manual_{lang}.md'
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(translated)
            await query.message.reply_document(document=open(path, 'rb'),
                                            filename=f'Handbuch_{lang}.md')
        finally:
            try:
                import os; os.remove(path)
            except Exception:
                pass
        return

    if m_notes:
        cid = int(m_notes.group(1))
        lang = get_group_language(cid) or "de"
        notes_text = PATCH_NOTES if lang == 'de' else translate_hybrid(PATCH_NOTES, target_lang=lang)
        text = f"📝 <b>Patchnotes v{__version__}</b>\n\n{notes_text}"
        await query.message.reply_text(text, parse_mode="HTML")
        return
    
    # Fallback: Hauptmenü der aktuell gewählten Gruppe
    cid = context.user_data.get('selected_chat_id', cid)
    return await show_group_menu(query=query, cid=cid, context=context)

# ======================================
# Freitext-Handler (ForceReply/Antworten)
# ======================================

async def menu_free_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = (msg.text or msg.caption or "").strip()
    photo_id = msg.photo[-1].file_id if msg.photo else None
    doc_id = msg.document.file_id if msg.document else None
    media_id = photo_id or doc_id

    # Pendings laden (defensiv)
    try:
        pend = get_pending_inputs(msg.chat.id, update.effective_user.id) or {}
        if not isinstance(pend, dict):
            pend = {}
        if 'rss_url' in pend:
            return  # RSS-Handler übernimmt
    except Exception as e:
        logger.warning(f"pending_inputs read failed: {e}")
        pend = {}

    # --- DB-Fallback: offener Edit-Flow? (Einzelabruf) ---
    if 'last_edit' not in context.user_data:
        e_pend = get_pending_input(msg.chat.id, update.effective_user.id, "edit")  # <-- Import wird hier genutzt
        if e_pend and isinstance(e_pend, dict) and e_pend.get("what") and e_pend.get("target_chat_id"):
            context.user_data['last_edit'] = (int(e_pend["target_chat_id"]), e_pend["what"])
            clear_pending_input(msg.chat.id, update.effective_user.id, "edit")
    
    # last_edit aus DB-Pending wiederherstellen
    if 'last_edit' not in context.user_data and 'edit' in pend:
        payload = pend.get('edit') or {}
        if isinstance(payload, dict) and payload.get('target_chat_id') and payload.get('what'):
            context.user_data['last_edit'] = (int(payload['target_chat_id']), payload['what'])
            clear_pending_input(msg.chat.id, update.effective_user.id, 'edit')

    # --- last_edit Flows ---
    if 'last_edit' in context.user_data:
        cid, what = context.user_data.pop('last_edit')
        try:
            if what == 'welcome':
                await _call_db_safe(set_welcome, cid, media_id, text)
                return await msg.reply_text("✅ Begrüßung gespeichert.")
            if what == 'rules':
                await _call_db_safe(set_rules, cid, media_id, text)
                return await msg.reply_text("✅ Regeln gespeichert.")
            if what == 'farewell':
                await _call_db_safe(set_farewell, cid, media_id, text)
                return await msg.reply_text("✅ Abschied gespeichert.")
        except Exception as e:
            return await msg.reply_text(f"⚠️ Fehler beim Speichern: {e}")

    # Nachtmodus-Zeiten
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

    # Linksperre-Warntext
    if context.user_data.pop('awaiting_link_warn', False) or ('link_warn' in pend):
        cid = context.user_data.pop('link_warn_group', (pend.get('link_warn') or {}).get('chat_id'))
        await _call_db_safe(set_link_settings, cid, warning_text=text)
        clear_pending_input(msg.chat.id, update.effective_user.id, 'link_warn')
        return await msg.reply_text("✅ Warn-Text gespeichert.")

    # Mood-Frage
    if context.user_data.pop('awaiting_mood_question', False) or ('mood_q' in pend):
        cid = context.user_data.pop('mood_group_id', (pend.get('mood_q') or {}).get('chat_id'))
        if not cid:
            return await msg.reply_text("⚠️ Keine Gruppe ausgewählt.")
        try:
            await _call_db_safe(set_mood_question, cid, text)
            clear_pending_input(msg.chat.id, update.effective_user.id, 'mood_q')
            return await msg.reply_text("✅ Mood-Frage gespeichert.")
        except Exception as e:
            return await msg.reply_text(f"⚠️ Fehler beim Speichern der Mood-Frage: {e}")

    # Router: delete
    if context.user_data.pop('awaiting_router_delete', False) or ('router_delete' in pend):
        cid = context.user_data.pop('router_group_id', (pend.get('router_delete') or {}).get('chat_id'))
        if not text.isdigit():
            return await msg.reply_text("Bitte eine numerische Regel-ID senden.")
        from database import delete_topic_router_rule
        delete_topic_router_rule(cid, int(text))
        clear_pending_input(msg.chat.id, update.effective_user.id, 'router_delete')
        return await msg.reply_text("🗑 Regel gelöscht.")

    # Router: toggle
    if context.user_data.pop('awaiting_router_toggle', False) or ('router_toggle' in pend):
        cid = context.user_data.pop('router_group_id', (pend.get('router_toggle') or {}).get('chat_id'))
        m = re.match(r'^\s*(\d+)\s+(on|off)\s*$', text, re.I)
        if not m:
            return await msg.reply_text("Format: <regel_id> on|off")
        rid = int(m.group(1)); on = m.group(2).lower() == "on"
        from database import toggle_topic_router_rule
        toggle_topic_router_rule(cid, rid, on)
        clear_pending_input(msg.chat.id, update.effective_user.id, 'router_toggle')
        return await msg.reply_text("🔁 Regel umgeschaltet.")

    # FAQ add: akzeptiere ";", "->", "→", "⟶"
    if context.user_data.pop('awaiting_faq_add', False) or ('faq_add' in pend):
        cid = context.user_data.pop('faq_group_id', (pend.get('faq_add') or {}).get('chat_id'))
        parts = re.split(r'\s*(?:;|->|→|⟶)\s*', (text or ""), maxsplit=1)
        if len(parts) != 2:
            return await msg.reply_text("Format: Stichwort ⟶ Antwort")
        await _call_db_safe(upsert_faq, cid, parts[0], parts[1])
        clear_pending_input(msg.chat.id, update.effective_user.id, 'faq_add')
        return await msg.reply_text("✅ FAQ gespeichert.")

    # FAQ delete
    if context.user_data.pop('awaiting_faq_del', False) or ('faq_del' in pend):
        cid = context.user_data.pop('faq_group_id', (pend.get('faq_del') or {}).get('chat_id'))
        await _call_db_safe(delete_faq, cid, text.strip())
        clear_pending_input(msg.chat.id, update.effective_user.id, 'faq_del')
        return await msg.reply_text("✅ FAQ gelöscht.")

    # Spam: Topic-Limit
    if context.user_data.pop('awaiting_topic_limit', False):
        cid = context.user_data.pop('spam_group_id')
        tid = context.user_data.pop('spam_topic_id')
        try:
            limit = int((update.effective_message.text or "").strip())
        except:
            return await update.effective_message.reply_text("Bitte eine Zahl senden.")
        set_spam_policy_topic(cid, tid, per_user_daily_limit=max(0, limit))
        return await update.effective_message.reply_text(f"✅ Limit gesetzt: {limit}/Tag/User (Topic {tid}).")

    # Spam: Whitelist
    if context.user_data.pop('awaiting_spam_whitelist', False) or (pend.get('spam_edit', {}).get('which') == 'whitelist'):
        cid = context.user_data.pop('spam_group_id', pend.get('spam_edit', {}).get('chat_id'))
        tid = context.user_data.pop('spam_topic_id', pend.get('spam_edit', {}).get('topic_id', 0))
        doms = [d.strip().lower() for d in re.split(r"[,\s]+", text) if d.strip()]
        cur = get_spam_policy_topic(cid, tid) or {}
        cur.setdefault("link_whitelist", [])
        cur["link_whitelist"] = sorted(set(cur["link_whitelist"] + doms))
        set_spam_policy_topic(cid, tid, link_whitelist=cur["link_whitelist"])
        clear_pending_input(msg.chat.id, update.effective_user.id, "spam_edit")
        return await msg.reply_text(f"✅ Whitelist aktualisiert ({len(cur['link_whitelist'])} Domains).")

    # Spam: Blacklist
    if context.user_data.pop('awaiting_spam_blacklist', False) or (pend.get('spam_edit', {}).get('which') == 'blacklist'):
        cid = context.user_data.pop('spam_group_id', pend.get('spam_edit', {}).get('chat_id'))
        tid = context.user_data.pop('spam_topic_id', pend.get('spam_edit', {}).get('topic_id', 0))
        doms = [d.strip().lower() for d in re.split(r"[,\s]+", text) if d.strip()]
        cur = get_spam_policy_topic(cid, tid) or {}
        cur.setdefault("domain_blacklist", [])
        cur["domain_blacklist"] = sorted(set(cur["domain_blacklist"] + doms))
        set_spam_policy_topic(cid, tid, domain_blacklist=cur["domain_blacklist"])
        clear_pending_input(msg.chat.id, update.effective_user.id, "spam_edit")
        return await msg.reply_text(f"✅ Blacklist aktualisiert ({len(cur['domain_blacklist'])} Domains).")

    # Router: Keywords / Domains (ohne Topic-Angabe → einfache Regeln)
    if context.user_data.pop('awaiting_router_add_keywords', False) or ('router_add_kw' in pend):
        cid = context.user_data.pop('router_group_id', pend.get('router_add_kw', {}).get('chat_id'))
        kws = [w.strip().lower() for w in re.split(r"[,\s]+", text) if w.strip()]
        from database import add_topic_router_rule
        target_tid = context.user_data.pop('router_target_tid', pend.get('router_add_kw', {}).get('target_tid'))
        for kw in kws:
            add_topic_router_rule(cid, keyword=kw, topic_id=target_tid)
        clear_pending_input(msg.chat.id, update.effective_user.id, "router_add_kw")
        return await msg.reply_text(f"✅ {len(kws)} Keyword(s) hinzugefügt.")

    if context.user_data.pop('awaiting_router_add_domains', False) or ('router_add_dom' in pend):
        cid = context.user_data.pop('router_group_id', pend.get('router_add_dom', {}).get('chat_id'))
        doms = [d.strip().lower() for d in re.split(r"[,\s]+", text) if d.strip()]
        from database import add_topic_router_rule
        target_tid = context.user_data.pop('router_target_tid', pend.get('router_add_dom', {}).get('target_tid'))
        for d in doms:
            add_topic_router_rule(cid, domain=d, topic_id=target_tid)
        clear_pending_input(msg.chat.id, update.effective_user.id, "router_add_dom")
        return await msg.reply_text(f"✅ {len(doms)} Domain(s) hinzugefügt.")

    # KI: Thresholds
    if context.user_data.pop('awaiting_aimod_thresholds', False):
        cid = context.user_data.pop('aimod_chat_id'); tid = context.user_data.pop('aimod_topic_id', 0)
        import json as _json
        try:
            data = _json.loads((update.effective_message.text or "").strip())
            set_ai_mod_settings(cid, tid,
                tox_thresh=float(data.get("tox", data.get("toxicity", 0.9))),
                hate_thresh=float(data.get("hate", 0.85)),
                sex_thresh=float(data.get("sex", 0.9)),
                harass_thresh=float(data.get("harass", 0.9)),
                selfharm_thresh=float(data.get("self", 0.95)),
                violence_thresh=float(data.get("viol", 0.9)),
                link_risk_thresh=float(data.get("link", 0.95)),
            )
            return await update.effective_message.reply_text("✅ Schwellen gespeichert.")
        except Exception:
            return await update.effective_message.reply_text("❌ Ungültiges JSON.")

    # KI: Warntext
    if context.user_data.pop('awaiting_aimod_warn', False):
        cid = context.user_data.pop('aimod_chat_id'); tid = context.user_data.pop('aimod_topic_id', 0)
        set_ai_mod_settings(cid, tid, warn_text=(update.effective_message.text or "").strip())
        return await update.effective_message.reply_text("✅ Warn-Text gespeichert.")

    # KI: Appeal-URL
    if context.user_data.pop('awaiting_aimod_appeal', False):
        cid = context.user_data.pop('aimod_chat_id'); tid = context.user_data.pop('aimod_topic_id', 0)
        url = (update.effective_message.text or "").strip()
        set_ai_mod_settings(cid, tid, appeal_url=url if url else None)
        return await update.effective_message.reply_text("✅ Appeal-URL gespeichert.")

    # KI: Rate/Cooldown/Mute
    if context.user_data.pop('awaiting_aimod_rate', False):
        cid = context.user_data.pop('aimod_chat_id'); tid = context.user_data.pop('aimod_topic_id', 0)
        params = {k: v for a in (update.effective_message.text or "").split() if "=" in a for k, v in [a.split("=", 1)]}
        fields = {}
        if "max_per_min" in params: fields["max_calls_per_min"] = int(params["max_per_min"])
        if "cooldown_s" in params:  fields["cooldown_s"] = int(params["cooldown_s"])
        if "mute_minutes" in params: fields["mute_minutes"] = int(params["mute_minutes"])
        if fields:
            set_ai_mod_settings(cid, tid, **fields)
            return await update.effective_message.reply_text("✅ Limits gespeichert.")
        return await update.effective_message.reply_text("❌ Format: max_per_min=20 cooldown_s=30 mute_minutes=60")

    if context.user_data.pop('awaiting_aimod_strike_cfg', False):
        cid = context.user_data.pop('aimod_chat_id'); key = context.user_data.pop('aimod_key')
        try:
            val = int((update.effective_message.text or "").strip())
        except:
            return await update.effective_message.reply_text("Bitte eine Zahl senden.")
        set_ai_mod_settings(cid, 0, **{key: val})
        return await update.effective_message.reply_text("✅ Gespeichert.")

# ============
# Registrierung
# ============

def register_menu(app):
    # Callback-Handler (Gruppe 0 – hohe Priorität)
    app.add_handler(CallbackQueryHandler(menu_callback), group=0)

    # Reply-Handler (Gruppe 1) – nur Replies, keine Commands
    app.add_handler(MessageHandler(
        filters.REPLY
        & (filters.TEXT | filters.PHOTO | filters.Document.ALL)
        & ~filters.COMMAND
        & (filters.ChatType.GROUPS | filters.ChatType.PRIVATE),
        menu_free_text_handler
    ), group=0)
