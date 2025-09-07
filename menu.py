# menu.py – Greeny Group Manager
# ------------------------------------------------------------
# Zweck:
# - Zentrales Inline-Menü für Gruppenverwaltung (Willkommen/Regeln/Abschied,
#   Captcha, Spamfilter, Nachtmodus, Topic-Router, RSS, FAQ, KI, Stats, etc.)
# - Einheitliche, robuste Callback-Logik
# - Klare Abschnitts-Kommentare und konsistentes Error-Handling
# ------------------------------------------------------------

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Update, CallbackQuery
from telegram.ext import CallbackQueryHandler, filters, MessageHandler, ContextTypes
from telegram.error import BadRequest
import re
import logging

# -----------------------------
# DB/Service-Importe (bereinigt)
# -----------------------------
from database import (
    get_link_settings, set_link_settings, _call_db_safe,
    get_welcome, set_welcome, delete_welcome,
    get_rules, set_rules, delete_rules,
    get_captcha_settings, set_captcha_settings,
    get_farewell, set_farewell, delete_farewell,
    get_rss_topic, list_rss_feeds as db_list_rss_feeds, remove_rss_feed,
    get_ai_settings, set_ai_settings, add_topic_router_rule,
    is_daily_stats_enabled, set_daily_stats,
    get_mood_question, set_mood_question, get_mood_topic,
    list_faqs, upsert_faq, delete_faq, set_clean_deleted_settings, 
    get_group_language, set_group_language,
    list_forum_topics, count_forum_topics, get_topic_owners,
    get_night_mode, set_night_mode, add_rss_feed, get_clean_deleted_settings,
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
from jobs import schedule_cleanup_for_chat, job_cleanup_deleted

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
         InlineKeyboardButton(tr('Aufräumen', lang), callback_data=f"{cid}:clean")],
        [InlineKeyboardButton(tr('📖 Handbuch', lang), callback_data=f"{cid}_help"),
         InlineKeyboardButton(tr('📝 Patchnotes', lang), callback_data=f"{cid}_patchnotes")]
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

async def _render_faq_menu(cid, query, context):
    lang = get_group_language(cid) or 'de'
    faqs = list_faqs(cid) or []
    ai_faq, _ = get_ai_settings(cid)
    lines = [f"• <code>{t}</code> → {a[:30]}..." for t, a in faqs[:10]]
    text = (
        "❓ <b>FAQ-System</b>\n\n"
        "📝 <b>Hinzufügen:</b> <code>Trigger ; Antwort</code>\n"
        "Beispiel: <code>hilfe ; Für Unterstützung schreibe @admin</code>\n\n"
        "🔍 <b>Auslösung:</b> Wenn Nutzer 'hilfe' schreibt oder fragt\n\n"
        "🤖 <b>KI-Fallback:</b> Bei unbekannten Fragen automatische Antworten\n\n"
        "<b>Aktuelle FAQs:</b>\n" + ("\n".join(lines) if lines else "Noch keine Einträge.")
    )
    kb = [
        [InlineKeyboardButton("➕ FAQ hinzufügen", callback_data=f"{cid}_faq_add"),
         InlineKeyboardButton("🗑 FAQ löschen", callback_data=f"{cid}_faq_del")],
        [InlineKeyboardButton(f"{'✅' if ai_faq else '☐'} KI-Fallback", callback_data=f"{cid}_faq_ai_toggle")],
        [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
    ]
    return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def _render_mood_menu(cid, query, context):
    lang = get_group_language(cid) or "de"
    q = get_mood_question(cid) or tr('Wie fühlst du dich heute?', lang)
    topic_id = get_mood_topic(cid)
    topic_txt = str(topic_id) if topic_id else tr('kein Topic gesetzt', lang)
    text = (
        "🧠 <b>Mood</b>\n"
        f"• Topic: <code>{topic_txt}</code>\n"
        f"• Frage: {q}\n\n"
        "Aktion wählen:"
    )
    kb = [
        [InlineKeyboardButton(tr('Jetzt senden', lang), callback_data=f"{cid}_mood_send")],
        [InlineKeyboardButton(tr('Frage ändern', lang), callback_data=f"{cid}_mood_edit_q")],
        [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
    ]
    return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def _render_clean_menu(cid, query, context):
    s = get_clean_deleted_settings(cid)
    hh = s["hh"] if s["hh"] is not None else 3
    mm = s["mm"] if s["mm"] is not None else 0
    weekday = s["weekday"]  # None = täglich
    demote = "✅" if s["demote"] else "❌"
    enabled = "✅" if s["enabled"] else "❌"

    kb = [
        [InlineKeyboardButton(f"Status: {'AN' if s['enabled'] else 'AUS'}", callback_data=f"{cid}_clean_toggle")],
        [InlineKeyboardButton(f"Zeit: {hh:02d}:{mm:02d}", callback_data=f"{cid}_clean_settime")],
        [InlineKeyboardButton(f"Rhythmus: {'täglich' if weekday is None else ['Mo','Di','Mi','Do','Fr','Sa','So'][weekday]}",
                              callback_data=f"{cid}_clean_setfreq")],
        [InlineKeyboardButton(f"Demote Admins: {demote}", callback_data=f"{cid}_clean_demote")],
        [InlineKeyboardButton("▶️ Jetzt ausführen", callback_data=f"{cid}_clean_run")],
        [InlineKeyboardButton("↩️ Zurück", callback_data=f"group_{cid}")]
    ]
    text = (
        "🧹 <b>Auto-Aufräumen gelöschter Accounts</b>\n\n"
        f"Status: {enabled}\nZeit: {hh:02d}:{mm:02d}\nRhythmus: "
        f"{'täglich' if weekday is None else ['Mo','Di','Mi','Do','Fr','Sa','So'][weekday]}\n"
        f"Admins demoten: {demote}"
    )
    return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def _render_ai_menu(cid, query, context):
    lang = get_group_language(cid) or 'de'
    ai_faq, ai_rss = get_ai_settings(cid)
    text = "🤖 <b>KI-Einstellungen</b>"
    kb = [
        [InlineKeyboardButton(f"{'✅' if ai_faq else '☐'} KI-FAQ", callback_data=f"{cid}_ai_faq_toggle"),
         InlineKeyboardButton(f"{'✅' if ai_rss else '☐'} KI-RSS", callback_data=f"{cid}_ai_rss_toggle")],
        [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
    ]
    return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


async def _render_aimod_menu(cid, query, context):
    lang = get_group_language(cid) or 'de'
    pol = effective_ai_mod_policy(cid, 0)  # deine bestehende Policy-Funktion
    text = "🛡️ <b>KI-Moderation</b>\nStelle Schwellwerte & Aktionen ein."
    kb = [
        [InlineKeyboardButton(f"{'✅' if pol.get('enabled') else '☐'} Aktiv", callback_data=f"{cid}_aimod_toggle")],
        [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
    ]
    return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def _render_rss_root(query, cid, lang):
    ai_faq, ai_rss = get_ai_settings(cid)
    text = "📰 <b>RSS</b>\nVerwalte Feeds, Topic und KI-Optionen."
    kb = [
        [InlineKeyboardButton("➕ Feed hinzufügen", callback_data=f"{cid}_rss_setrss"),
         InlineKeyboardButton("📃 Feeds anzeigen", callback_data=f"{cid}_rss_list")],
        [InlineKeyboardButton(f"{'✅' if ai_rss else '☐'} KI-Zusammenfassung", callback_data=f"{cid}_rss_ai_toggle")],
        [InlineKeyboardButton("🧵 Topic setzen", callback_data=f"{cid}_rss_topic_set")],
        [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
    ]
    try:
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    except BadRequest as e:
        if "Message is not modified" in str(e):
            try: await query.answer("Keine Änderung.", show_alert=False)
            except Exception: pass
            return
        raise
    
async def _render_rss_list(query, cid, lang):
    feeds = db_list_rss_feeds(cid) or []
    if not feeds:
        kb = [[InlineKeyboardButton('↩️ Zurück', callback_data=f'group_{cid}')]]
        return await query.edit_message_text('Keine RSS-Feeds.', reply_markup=InlineKeyboardMarkup(kb))

    rows = []
    text_lines = ["📰 <b>Aktive Feeds</b>:"]
    for item in feeds:
        url = item[0]
        tid = item[1] if len(item) > 1 else "?"
        opts = get_rss_feed_options(cid, url) or {}
        img_on = bool(opts.get("post_images", False))
        text_lines.append(f"• {url} (Topic {tid})")
        rows.append([
            InlineKeyboardButton(f"🖼 Bilder: {'AN' if img_on else 'AUS'}", callback_data=f"{cid}_rss_img_toggle|{url}"),
            InlineKeyboardButton("🗑 Entfernen", callback_data=f"{cid}_rss_del|{url}")
        ])
    rows.append([InlineKeyboardButton('↩️ Zurück', callback_data=f'{cid}_rss')])
    return await query.edit_message_text(
        "\n".join(text_lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML"
    )

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
    ls = get_link_settings(cid) or {}
    prot_on = bool(ls.get("only_admin_links") or ls.get("admins_only") or ls.get("protection"))

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
        [InlineKeyboardButton("📝 Whitelist", callback_data=f"{cid}_spam_wl_edit_0"),
         InlineKeyboardButton("❌ Blacklist", callback_data=f"{cid}_spam_bl_edit_0")],
        [InlineKeyboardButton(f"{'✅' if prot_on else '☐'} 🔗 Nur Admin-Links (Gruppe)",
                              callback_data=f"{cid}_spam_link_admins_global")],
        [InlineKeyboardButton("✏️ Warntext (Gruppe)", callback_data=f"{cid}_spam_link_warn_global")],
        [InlineKeyboardButton("🎯 Topic-Regeln", callback_data=f"{cid}_spam_tsel")],
        [InlineKeyboardButton("❓ Hilfe", callback_data=f"{cid}_spam_help")],
        [InlineKeyboardButton(tr('↩️ Zurück', lang), callback_data=f"group_{cid}")]
    ]
    try:
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    except BadRequest as e:
        if "Message is not modified" in str(e):
            # einfach kurz bestätigen, ohne zu crashen
            try:
                await query.answer(tr('Keine Änderung.', lang), show_alert=False)
            except Exception:
                pass
            return
        raise

async def _render_spam_topic(query, cid, topic_id):
    pol   = get_spam_policy_topic(cid, topic_id) or {}
    level = pol.get('level', 'off')
    emsg  = pol.get('emoji_max_per_msg', 0) or 0
    rate  = pol.get('max_msgs_per_10s', 0) or 0
    limit = pol.get('per_user_daily_limit', 0) or 0
    qmode = (pol.get('quota_notify') or 'smart')
    wl_list = pol.get('link_whitelist') or []
    bl_list = pol.get('domain_blacklist') or []
    wl = ", ".join(wl_list) if wl_list else "–"
    bl = ", ".join(bl_list) if bl_list else "–"

    # Ausnahmen (Topic-Owner) sicher ermitteln
    try:
        owners = get_topic_owners(cid, topic_id) or []
    except Exception:
        owners = []

    if owners:
        owner_lines = [f"• <a href='tg://user?id={uid}'>User {uid}</a>" for uid in owners]
        owners_text = "<b>Ausnahmen (Topic-Owner):</b>\n" + "\n".join(owner_lines)
    else:
        owners_text = "<b>Ausnahmen (Topic-Owner):</b> – keine –"

    text = (
        f"🧹 <b>Spamfilter – Topic {topic_id}</b>\n\n"
        f"Level: <b>{level}</b>\n"
        f"Emoji/Msg: <b>{emsg}</b> • Flood/10s: <b>{rate}</b>\n"
        f"Limit/Tag/User: <b>{limit}</b>\n"
        f"Rest-Info: <b>{qmode}</b>\n"
        f"Whitelist: {wl}\n"
        f"Blacklist: {bl}\n"
        f"{owners_text}"
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

async def cleanup_menu(query: CallbackQuery, cid: int, context):
    s = get_clean_deleted_settings(cid)
    hh = s["hh"] if s["hh"] is not None else 3
    mm = s["mm"] if s["mm"] is not None else 0
    weekday = s["weekday"]  # None = täglich
    demote = "✅" if s["demote"] else "❌"
    enabled = "✅" if s["enabled"] else "❌"

    kb = [
        [InlineKeyboardButton(f"Status: {'AN' if s['enabled'] else 'AUS'}", callback_data=f"group_{cid}:cleanup:toggle")],
        [InlineKeyboardButton(f"Zeit: {hh:02d}:{mm:02d}", callback_data=f"group_{cid}:cleanup:settime")],
        [InlineKeyboardButton(f"Rhythmus: {'täglich' if weekday is None else ['Mo','Di','Mi','Do','Fr','Sa','So'][weekday]}",
                              callback_data=f"group_{cid}:cleanup:setfreq")],
        [InlineKeyboardButton(f"Demote Admins: {demote}", callback_data=f"group_{cid}:cleanup:demote")],
        [InlineKeyboardButton("▶️ Jetzt ausführen", callback_data=f"group_{cid}:cleanup:run")],
        [InlineKeyboardButton("⬅️ Zurück", callback_data=f"group_{cid}")]
    ]
    await query.edit_message_text(
        f"🧹 *Auto-Aufräumen gelöschter Accounts*\n\n"
        f"Status: {enabled}\nZeit: {hh:02d}:{mm:02d}\nRhythmus: "
        f"{'täglich' if weekday is None else ['Mo','Di','Mi','Do','Fr','Sa','So'][weekday]}\n"
        f"Admins demoten: {demote}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )

async def menu_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    data = query.data
    # group_-100123456789
    if data.startswith("group_") and ":cleanup" not in data:
        cid = int(data.split("_",1)[1])
        context.user_data["selected_chat_id"] = cid
        return await show_group_menu(query=query, cid=cid, context=context, dest_chat_id=query.message.chat_id)

    if data.startswith("group_") and ":cleanup" in data:
        parts = data.split(":")
        cid = int(parts[0].split("_",1)[1])
        action = parts[2] if len(parts) >= 3 else None

        if action is None:
            return await cleanup_menu(query, cid, context)

        if action == "toggle":
            s = get_clean_deleted_settings(cid)
            set_clean_deleted_settings(cid, enabled=not s["enabled"])
            schedule_cleanup_for_chat(context.application.job_queue, cid)   # << neu einplanen
            return await cleanup_menu(query, cid, context)

        if action == "settime":
            # Beispiel: zyklisch auf +1 Stunde erhöhen (einfachste UI ohne freie Eingabe)
            s = get_clean_deleted_settings(cid)
            hh = ( (s["hh"] if s["hh"] is not None else 3) + 1 ) % 24
            set_clean_deleted_settings(cid, hh=hh, mm=s["mm"] or 0)
            schedule_cleanup_for_chat(context.application.job_queue, cid)
            return await cleanup_menu(query, cid, context)

        if action == "setfreq":
            s = get_clean_deleted_settings(cid)
            # Toggle: täglich -> Mo (0) -> Di (1) -> ... -> So (6) -> täglich
            wd = s["weekday"]
            new_wd = 0 if wd is None else (None if wd==6 else wd+1)
            set_clean_deleted_settings(cid, weekday=new_wd)
            schedule_cleanup_for_chat(context.application.job_queue, cid)
            return await cleanup_menu(query, cid, context)

        if action == "demote":
            s = get_clean_deleted_settings(cid)
            set_clean_deleted_settings(cid, demote=not s["demote"])
            schedule_cleanup_for_chat(context.application.job_queue, cid)
            return await cleanup_menu(query, cid, context)

        if action == "run":
            await query.answer("Job läuft …", show_alert=False)
            await job_cleanup_deleted(context=type("Obj",(object,),{"job":type("J",(object,),{"chat_id":cid,"data":None})(), "bot":context.bot})())
            return await cleanup_menu(query, cid, context)

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
        return await _render_rss_root(query, cid, lang)

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
        # KI-Fallback Toggle
        if sub == 'ai_toggle':
            ai_faq, _ = get_ai_settings(cid)
            set_ai_settings(cid, faq=not ai_faq)
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
            return await _render_faq_menu(cid, query, context)

        # Hinzufügen
        if sub == 'add':
            context.user_data.update(awaiting_faq_add=True, faq_group_id=cid)
            set_pending_input(query.message.chat.id, update.effective_user.id, "faq_add", {"chat_id": cid})
            return await query.message.reply_text("Format: Trigger ; Antwort", reply_markup=ForceReply(selective=True))

        # Löschen
        if sub == 'del':
            context.user_data.update(awaiting_faq_del=True, faq_group_id=cid)
            set_pending_input(query.message.chat.id, update.effective_user.id, "faq_del", {"chat_id": cid})
            return await query.message.reply_text("Bitte nur den Trigger senden (genau wie eingetragen).", reply_markup=ForceReply(selective=True))        
        
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

    if func == 'clean' and sub is None:
        return await _render_clean_menu(cid, query, context)

    if func == 'clean' and sub:
        if sub == 'toggle':
            s = get_clean_deleted_settings(cid)
            set_clean_deleted_settings(cid, enabled=not s["enabled"])
            schedule_cleanup_for_chat(context.application.job_queue, cid)
            return await _render_clean_menu(cid, query, context)

        if sub == 'settime':
            s = get_clean_deleted_settings(cid)
            hh = ((s["hh"] if s["hh"] is not None else 3) + 1) % 24  # simpel: +1h pro Klick
            set_clean_deleted_settings(cid, hh=hh, mm=s["mm"] or 0)
            schedule_cleanup_for_chat(context.application.job_queue, cid)
            return await _render_clean_menu(cid, query, context)

        if sub == 'setfreq':
            s = get_clean_deleted_settings(cid)
            wd = s["weekday"]
            new_wd = 0 if wd is None else (None if wd == 6 else wd + 1)  # täglich → Mo → … → So → täglich
            set_clean_deleted_settings(cid, weekday=new_wd)
            schedule_cleanup_for_chat(context.application.job_queue, cid)
            return await _render_clean_menu(cid, query, context)

        if sub == 'demote':
            s = get_clean_deleted_settings(cid)
            set_clean_deleted_settings(cid, demote=not s["demote"])
            schedule_cleanup_for_chat(context.application.job_queue, cid)
            return await _render_clean_menu(cid, query, context)

        if sub == 'run':
            # Sofort-Bereinigung (wie bei deinen anderen „Jetzt ausführen“-Aktionen)
            await query.answer("Job läuft …", show_alert=False)
            removed = await clean_delete_accounts_for_chat(cid, context.bot)
            try:
                await context.bot.send_message(cid, f"🧹 Auto-Aufräumen: {removed} gelöschte Accounts entfernt.")
            except Exception:
                pass
            return await _render_clean_menu(cid, query, context)
    
    if func == 'night' and sub:
        en, s, e, del_non_admin, warn_once, tz, hard_mode, override_until = get_night_mode(cid)

        if sub == 'toggle':
            set_night_mode(cid, enabled=not en)
            await query.answer(f"{'Aktiviert' if not en else 'Deaktiviert'}", show_alert=True)
            return await menu_callback(update, context)  # oder _render_night_root, falls vorhanden

        if sub == 'hard_toggle':
            set_night_mode(cid, hard_mode=not hard_mode)
            await query.answer(f"Harter Modus: {'AN' if not hard_mode else 'AUS'}", show_alert=True)
            return await menu_callback(update, context)

        if sub == 'del_toggle':
            set_night_mode(cid, delete_non_admin=not del_non_admin)
            await query.answer(f"Nicht-Admin löschen: {'AN' if not del_non_admin else 'AUS'}", show_alert=True)
            return await menu_callback(update, context)

        if sub == 'warnonce_toggle':
            set_night_mode(cid, warn_once=not warn_once)
            await query.answer(f"Einmal warnen: {'AN' if not warn_once else 'AUS'}", show_alert=True)
            return await menu_callback(update, context)

        if sub.startswith('quiet_'):
            import datetime
            from zoneinfo import ZoneInfo
            now = datetime.datetime.now(ZoneInfo(tz or "Europe/Berlin"))
            mins = {'15m': 15, '1h': 60, '8h': 8*60}[sub.split('_',1)[1]]
            until = now + datetime.timedelta(minutes=mins)
            set_night_mode(cid, override_until=until.astimezone(datetime.timezone.utc))
            await query.answer(f"Sofortige Ruhe bis {until.strftime('%d.%m. %H:%M')}", show_alert=True)
            return await menu_callback(update, context)

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
        # KI – Aktionen
        if func == 'ai' and sub:
            if sub == 'faq_toggle':
                ai_faq, ai_rss = get_ai_settings(cid)
                set_ai_settings(cid, faq=not ai_faq)
                await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
                return await _render_ai_menu(cid, query, context)

            if sub == 'rss_toggle':
                ai_faq, ai_rss = get_ai_settings(cid)
                set_ai_settings(cid, rss=not ai_rss)
                await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
                return await _render_ai_menu(cid, query, context)
        
        # KI-Moderation – Aktionen
        if func == 'aimod' and sub:
            if sub == 'toggle':
                pol = effective_ai_mod_policy(cid, 0)
                set_ai_mod_settings(cid, 0, enabled=not pol.get('enabled', False))
                await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
                return await _render_aimod_menu(cid, query, context)    
            
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
            set_pending_input(query.message.chat.id, update.effective_user.id, "rss_url", {"chat_id": cid})
            await query.message.reply_text('📰 Bitte sende die RSS-URL:', reply_markup=ForceReply(selective=True))
            await query.answer("Sende nun die RSS-URL als Antwort.")
            return  

        if sub == 'list':
            feeds = db_list_rss_feeds(cid) or []
            if not feeds:
                kb = [[InlineKeyboardButton('↩️ Zurück', callback_data=f'{cid}_rss')]]
                return await query.edit_message_text('Keine RSS-Feeds.', reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
            rows = []
            text_lines = ["📰 <b>Aktive Feeds</b>:"]
            for item in feeds:
                url = item[0]
                tid = item[1] if len(item) > 1 else "?"
                opts = get_rss_feed_options(cid, url) or {}
                img_on = bool(opts.get("post_images", False))
                text_lines.append(f"• {url} (Topic {tid})")
                rows.append([
                    InlineKeyboardButton(f"🖼 Bilder: {'AN' if img_on else 'AUS'}", callback_data=f"{cid}_rss_img_toggle|{url}"),
                    InlineKeyboardButton("🗑 Entfernen", callback_data=f"{cid}_rss_del|{url}")
                ])
            rows.append([InlineKeyboardButton('↩️ Zurück', callback_data=f'{cid}_rss')])
            return await query.edit_message_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")
        
        if sub == 'stop':
            # stoppt alle Feeds der Gruppe
            remove_rss_feed(cid)
            await query.answer('✅ RSS gestoppt', show_alert=True)
            return await _render_rss_root(query, cid, lang)

        if sub == 'ai_toggle':
            ai_faq, ai_rss = get_ai_settings(cid)
            set_ai_settings(cid, rss=not ai_rss)
            log_feature_interaction(cid, update.effective_user.id, "menu:rss", {"action": "ai_toggle", "from": ai_rss, "to": (not ai_rss)})
            await query.answer(tr('Einstellung gespeichert.', lang), show_alert=True)
            return await _render_rss_root(query, cid, lang)

        if sub == 'topic_set':
            await query.answer('Öffne den gewünschten Foren-Thread und sende dort /settopicrss.', show_alert=True)
            return await _render_rss_root(query, cid, lang)
        
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
            return await _render_rss_list(query, cid, lang)

    # --- Spam ---
    if func == 'spam' and sub is None:
        return await _render_spam_root(query, cid, lang)

    if func == 'spam' and sub and sub.startswith('t_'):
        topic_id = int(sub.split('_', 1)[1])
        return await _render_spam_topic(query, cid, topic_id)
    
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

        if sub == 'link_admins_global':
            ls = get_link_settings(cid) or {}
            cur = bool(ls.get("only_admin_links") or ls.get("admins_only") or ls.get("protection"))
            # wahlweise synchron:
            set_link_settings(cid, protection=not cur)
            # oder wenn du konsequent _call_db_safe nutzt:
            # await _call_db_safe(set_link_settings, cid, protection=not cur)

            await query.answer(f"Nur Admin-Links: {'AN' if not cur else 'AUS'}", show_alert=True)
            return await _render_spam_root(query, cid)
        
        if sub == 'help':
            txt = ("🧹 <b>Spamfilter – Hilfe</b>\n\n"
                "• Level: off/light/medium/strict\n"
                "• Emoji- & Flood-Limits: pro Topic anpassbar\n"
                "• Whitelist/Blacklist: Domains erlauben/verbieten\n"
                "• Tageslimit/Benachrichtigung: pro Topic/Benutzer\n")
            return await query.edit_message_text(txt, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Zurück", callback_data=f"{cid}_spam")]]))
    
        if sub == 'link_warn_global':
            # globaler Warntext → Pending setzen
            context.user_data['awaiting_link_warn'] = True
            context.user_data['link_warn_group'] = cid
            set_pending_input(query.message.chat.id, update.effective_user.id,
                            "link_warn", {"chat_id": cid})
            await query.message.reply_text("Bitte neuen Warntext (Gruppe) senden:",
                                        reply_markup=ForceReply(selective=True))
            return

        if sub.startswith('setlvl_'):
            topic_id = int(sub.split('_', 1)[1])
            pol = get_spam_policy_topic(cid, topic_id) or {'level': 'off'}
            order = ['off', 'light', 'medium', 'strict']
            nxt = order[(order.index(pol.get('level', 'off')) + 1) % len(order)]
            set_spam_policy_topic(cid, topic_id, level=nxt)
            await query.answer(f"Level: {nxt}", show_alert=True)
            return await _render_spam_topic(query, cid, topic_id)

        # Einheitlich und sicher parsen:
        if sub.startswith(('emj_', 'rate_', 'wl_edit_', 'bl_edit_', 'limt_edit_', 'qmode_')):
            parts = sub.split('_')
            head = parts[0]

            if head == 'emj':
                op, topic_id = parts[1], int(parts[2])
                pol = get_spam_policy_topic(cid, topic_id) or {}
                cur = (pol.get('emoji_max_per_msg') or 0) + (1 if op == '+' else -1)
                set_spam_policy_topic(cid, topic_id, emoji_max_per_msg=max(0, cur))
                return await _render_spam_topic(query, cid, topic_id)

            if head == 'rate':
                op, topic_id = parts[1], int(parts[2])
                pol = get_spam_policy_topic(cid, topic_id) or {}
                cur = (pol.get('max_msgs_per_10s') or 0) + (1 if op == '+' else -1)
                set_spam_policy_topic(cid, topic_id, max_msgs_per_10s=max(0, cur))
                return await _render_spam_topic(query, cid, topic_id)

            if head in ('wl', 'bl') and len(parts) >= 3 and parts[1] == 'edit':
                topic_id = int(parts[2])
                which = 'whitelist' if head == 'wl' else 'blacklist'
                context.user_data.update(
                    awaiting_spam_whitelist=(which == 'whitelist'),
                    awaiting_spam_blacklist=(which == 'blacklist'),
                    spam_group_id=cid, spam_topic_id=topic_id
                )
                set_pending_input(
                    query.message.chat.id, update.effective_user.id, "spam_edit",
                    {"chat_id": cid, "topic_id": topic_id, "which": which}
                )
                prompt = "Sende die Whitelist-Domains, Komma-getrennt:" if which == 'whitelist' else "Sende die Blacklist-Domains, Komma-getrennt:"
                return await query.message.reply_text(prompt, reply_markup=ForceReply(selective=True))

            if head == 'limt' and len(parts) >= 3 and parts[1] == 'edit':
                topic_id = int(parts[2])
                context.user_data.update(awaiting_topic_limit=True, spam_group_id=cid, spam_topic_id=topic_id)
                set_pending_input(query.message.chat.id, update.effective_user.id, "spam_edit",
                                {"chat_id": cid, "topic_id": topic_id, "which": "limit"})
                return await query.message.reply_text("Bitte Limit/Tag/User als Zahl senden (0 = aus):", reply_markup=ForceReply(selective=True))

            if head == 'qmode' and len(parts) >= 2:
                topic_id = int(parts[1])  # ← WICHTIG: qmode_{id} hat nur 2 Teile
                pol = get_spam_policy_topic(cid, topic_id) or {'quota_notify': 'smart'}
                order = ['off', 'smart', 'always']
                cur = (pol.get('quota_notify') or 'smart').lower()
                nxt = order[(order.index(cur) + 1) % len(order)]
                set_spam_policy_topic(cid, topic_id, quota_notify=nxt)
                await query.answer(f"Rest-Info: {nxt}", show_alert=True)
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
    
    if func == 'aimod' and sub is None:
        return await _render_aimod_root(query, cid)
    
    if func == 'aimod' and sub:
        if sub == 'toggle':
            pol = effective_ai_mod_policy(cid, 0)
            set_ai_mod_settings(cid, 0, enabled=not pol['enabled'])
            return await _render_aimod_menu(cid, query, context)
        if sub == 'shadow':
            pol = effective_ai_mod_policy(cid, 0)
            set_ai_mod_settings(cid, 0, shadow_mode=not pol['shadow_mode'])
            return await _render_aimod_menu(cid, query, context)
        if sub == 'act':
            order = ['delete', 'warn', 'mute', 'ban']
            cur = effective_ai_mod_policy(cid, 0)['action_primary']
            nxt = order[(order.index(cur) + 1) % len(order)]
            set_ai_mod_settings(cid, 0, action_primary=nxt)
            return await _render_aimod_menu(cid, query, context)
        if sub == 'escal':
            order = ['mute', 'ban']
            cur = effective_ai_mod_policy(cid, 0)['escalate_action']
            nxt = order[(order.index(cur) + 1) % len(order)]
            set_ai_mod_settings(cid, 0, escalate_action=nxt)
            return await _render_aimod_menu(cid, query, context)
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
            topic_id = get_mood_topic(cid)
            if not topic_id:
                await query.answer(tr('❗ Kein Mood-Topic gesetzt. Sende /setmoodtopic im gewünschten Thema.', lang), show_alert=True)
                return await _render_mood_menu(cid, query, context)

            q = get_mood_question(cid) or tr('Wie fühlst du dich heute?', lang)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("👍", callback_data="mood_like"),
                InlineKeyboardButton("👎", callback_data="mood_dislike"),
                InlineKeyboardButton("🤔", callback_data="mood_think")]
            ])
            await context.bot.send_message(chat_id=cid, text=q, reply_markup=kb, message_thread_id=topic_id)
            await query.answer(tr('✅ Mood-Frage gesendet.', lang), show_alert=True)
            return await _render_mood_menu(cid, query, context)
        
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
    cid = msg.chat.id
    user_id = update.effective_user.id if update.effective_user else None
    text = (msg.text or msg.caption or "").strip()
    photo_id = msg.photo[-1].file_id if msg.photo else None
    doc_id = msg.document.file_id if msg.document else None
    media_id = photo_id or doc_id
    ud    = context.user_data or {}
    text = (msg.text or "").strip()
    # hole ALLE Pending-Keys (nicht nur einen)
    pend = get_pending_inputs(msg.chat.id, update.effective_user.id) or {}

    logger.info("menu_free_text: user=%s pend_keys=%s flags=%s text=%r",
            update.effective_user.id,
            list((pend or {}).keys()),
            [k for k in (ud or {}).keys() if k.startswith('awaiting_')],
            (text[:80] if text else text))

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

    # --- FAQ hinzufügen ---------------------------------------------------------
    if context.user_data.pop('awaiting_faq_add', False) or ('faq_add' in (pend or {})):
        cid = context.user_data.pop('faq_group_id', (pend or {}).get('faq_add', {}).get('chat_id'))
        parts = [p.strip() for p in text.split(";", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return await msg.reply_text("Format: Trigger ; Antwort")

        try:
            await _call_db_safe(upsert_faq, cid, parts[0], parts[1])
        except Exception:
            logger.exception("FAQ upsert failed")
            return await msg.reply_text("❌ Konnte die FAQ nicht speichern.")

        clear_pending_input(msg.chat.id, update.effective_user.id, 'faq_add')
        await msg.reply_text("✅ FAQ gespeichert.")
        return

    # --- FAQ löschen ------------------------------------------------------------
    if context.user_data.pop('awaiting_faq_del', False) or ('faq_del' in (pend or {})):
        cid = context.user_data.pop('faq_group_id', (pend or {}).get('faq_del', {}).get('chat_id'))
        trigger = text.strip()
        if not trigger:
            return await msg.reply_text("Bitte nur den Trigger senden (genau wie eingetragen).")

        try:
            await _call_db_safe(delete_faq, cid, trigger)
        except Exception:
            logger.exception("FAQ delete failed")
            return await msg.reply_text("❌ Konnte die FAQ nicht löschen.")

        clear_pending_input(msg.chat.id, update.effective_user.id, 'faq_del')
        await msg.reply_text("✅ FAQ gelöscht.")
        return

    # Topic-Limit (max Nachrichten/Tag)
    if ud.pop('awaiting_topic_limit', False) or ('topic_limit' in pend):
        cid = ud.pop('spam_group_id',  (pend.get('topic_limit') or {}).get('chat_id'))
        tid = ud.pop('spam_topic_id',  (pend.get('topic_limit') or {}).get('topic_id'))
        if not (cid and tid):
            return await msg.reply_text("⚠️ Kein Ziel erkannt. Menü: Spam → Topic → Limit/Tag.")

        try:
            val = int(text)
        except ValueError:
            return await msg.reply_text("Bitte Limit als Zahl senden (0 = aus).")

        try:
            await _call_db_safe(set_spam_policy_topic, cid, tid, per_user_daily_limit=val)
            clear_pending_input(msg.chat.id, update.effective_user.id, 'topic_limit')
            clear_pending_input(msg.chat.id, update.effective_user.id, 'spam_edit')  # ← ergänzen
            return await msg.reply_text(f"✅ Tageslimit für Topic {tid} gesetzt auf {val}.")
        except Exception:
            logger.exception("topic limit save failed")
            return await msg.reply_text("❌ Konnte Limit nicht speichern.")

    # Spam: Whitelist
    if ud.pop("awaiting_spam_whitelist", False):
        raw = (msg.text or "").strip()
        doms = sorted({d.lower().lstrip(".") for d in re.split(r"[,\s]+", raw) if d})
        pend = (context.chat_data.get("pending_inputs") or {}).get(user_id, {}) or {}
        topic_id = int((pend.get("spam_edit") or {}).get("topic_id") or 0)

        if topic_id > 0:
            cur = get_spam_policy_topic(cid, topic_id) or {}
            cur.setdefault("link_whitelist", [])
            new_wl = sorted(set((cur["link_whitelist"] or []) + doms))
            set_spam_policy_topic(cid, topic_id, link_whitelist=new_wl)
            await msg.reply_text(f"✅ Whitelist gespeichert ({len(new_wl)} Einträge) für Topic {topic_id}.")
            return await _render_spam_topic(query=None, cid=cid, topic_id=topic_id)
        else:
            # global → im Topic-Override „0“ persistieren
            cur = get_spam_policy_topic(cid, 0) or {}
            cur.setdefault("link_whitelist", [])
            new_wl = sorted(set((cur["link_whitelist"] or []) + doms))
            set_spam_policy_topic(cid, 0, link_whitelist=new_wl)
            await msg.reply_text(f"✅ Whitelist (global) gespeichert ({len(new_wl)} Einträge).")
            return await _render_spam_root(query=None, cid=cid)

    # Spam: Blacklist
    if ud.pop("awaiting_spam_blacklist", False):
        raw = (msg.text or "").strip()
        doms = sorted({d.lower().lstrip(".") for d in re.split(r"[,\s]+", raw) if d})
        pend = (context.chat_data.get("pending_inputs") or {}).get(user_id, {}) or {}
        topic_id = int((pend.get("spam_edit") or {}).get("topic_id") or 0)

        if topic_id > 0:
            cur = get_spam_policy_topic(cid, topic_id) or {}
            cur.setdefault("domain_blacklist", [])
            new_bl = sorted(set((cur["domain_blacklist"] or []) + doms))
            set_spam_policy_topic(cid, topic_id, domain_blacklist=new_bl)
            await msg.reply_text(f"✅ Blacklist gespeichert ({len(new_bl)} Einträge) für Topic {topic_id}.")
            return await _render_spam_topic(query=None, cid=cid, topic_id=topic_id)
        else:
            cur = get_spam_policy_topic(cid, 0) or {}
            cur.setdefault("domain_blacklist", [])
            new_bl = sorted(set((cur["domain_blacklist"] or []) + doms))
            set_spam_policy_topic(cid, 0, domain_blacklist=new_bl)
            await msg.reply_text(f"✅ Blacklist (global) gespeichert ({len(new_bl)} Einträge).")
            return await _render_spam_root(query=None, cid=cid)

    # Router: Keywords / Domains (ohne Topic-Angabe → einfache Regeln)
    if context.user_data.pop('awaiting_router_add_keywords', False) or ('router_add_kw' in (pend or {})):
        cid = context.user_data.pop('router_group_id', (pend.get('router_add_kw') or {}).get('chat_id'))
        tid = context.user_data.pop('router_target_tid', (pend.get('router_add_kw') or {}).get('target_tid'))
        kws = [w.strip().lower() for w in re.split(r"[,\s]+", text) if w.strip()]
        if not tid or not kws:
            return await msg.reply_text("Bitte Keywords angeben.")
        rid = add_topic_router_rule(cid, tid, keywords=kws)  # ✅ korrekt: (chat_id, target_topic_id, keywords=[...])
        clear_pending_input(msg.chat.id, update.effective_user.id, 'router_add_kw')
        return await msg.reply_text(f"✅ Regel #{rid} → Topic {tid} (Keywords) angelegt.")

    if context.user_data.pop('awaiting_router_add_domains', False) or ('router_add_dom' in (pend or {})):
        cid = context.user_data.pop('router_group_id', (pend.get('router_add_dom') or {}).get('chat_id'))
        tid = context.user_data.pop('router_target_tid', (pend.get('router_add_dom') or {}).get('target_tid'))
        doms = [d.strip().lower() for d in re.split(r"[,\s]+", text) if d.strip()]
        if not tid or not doms:
            return await msg.reply_text("Bitte Domains angeben.")
        rid = add_topic_router_rule(cid, tid, domains=doms)  # ✅ korrekt
        clear_pending_input(msg.chat.id, update.effective_user.id, 'router_add_dom')
        return await msg.reply_text(f"✅ Regel #{rid} → Topic {tid} (Domains) angelegt.")

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

    # 4.1 RSS-URL
    if context.user_data.pop('awaiting_rss_url', False) or ('rss_url' in (pend or {})):
        payload = (pend.get('rss_url') or {}) if isinstance(pend, dict) else {}
        # hol die Ziel-Gruppe robust aus user_data ODER Pending (chat_id bevorzugt, fallback target_chat_id)
        cid = context.user_data.pop('rss_group_id', None)
        if not cid:
            cid = payload.get('chat_id') or payload.get('target_chat_id')

        if not cid:
            return await msg.reply_text("⚠️ Kein Ziel-Chat erkannt. Menü: RSS → Feed hinzufügen.")

        url = text.strip()
        if not re.match(r'^https?://', url):
            return await msg.reply_text("Bitte eine gültige URL mit http(s) senden.")

        # topic_id aus den Gruppeneinstellungen holen
        try:
            topic_id = get_rss_topic(int(cid))
        except Exception:
            topic_id = 0

        if not topic_id:
            # Aufräumen & Hinweis
            clear_pending_input(msg.chat.id, update.effective_user.id, 'rss_url')
            return await msg.reply_text("❗ Kein RSS-Topic gesetzt. Bitte zuerst /settopicrss im gewünschten Thread ausführen.")

        try:
            await _call_db_safe(add_rss_feed, int(cid), url, int(topic_id))  # <- 3 Argumente!
            clear_pending_input(msg.chat.id, update.effective_user.id, 'rss_url')
            return await msg.reply_text(f"✅ RSS-Feed hinzugefügt (Topic {topic_id}):\n{url}")
        except Exception:
            logger.exception("rss add failed")
            return await msg.reply_text("❌ Konnte RSS-Feed nicht speichern.")

# ============
# Registrierung
# ============

def register_menu(app):
    # Callback-Handler (Gruppe 0 – hohe Priorität)
    app.add_handler(CallbackQueryHandler(menu_callback), group=0)
    # Reply-Handler (Gruppe 1) – nur Replies, keine Commands
    app.add_handler(MessageHandler(
        filters.REPLY & (filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND
        & (filters.ChatType.GROUPS | filters.ChatType.PRIVATE),
        menu_free_text_handler
    ), group=0)
