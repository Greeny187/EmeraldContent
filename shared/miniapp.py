import os, json, urllib.parse, logging
from typing import List, Tuple, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import CommandHandler, MessageHandler, ContextTypes, filters, Application

logger = logging.getLogger(__name__)

MINIAPP_URL = os.getenv(
    "MINIAPP_URL",
    "https://greeny187.github.io/EmeraldContentBots/miniapp/index.html"
)

# --- DB-Brücke: importiert alle genutzten Funktionen aus shared.database oder .database ---
def _db():
    try:
        from shared.database import (
            get_registered_groups,
            # Welcome/Rules/Farewell
            set_welcome, delete_welcome, get_welcome,
            set_rules, delete_rules, get_rules,
            set_farewell, delete_farewell, get_farewell,
            # Links/Spam
            get_link_settings, set_link_settings, set_spam_policy_topic,
            # RSS
            set_rss_topic, get_rss_topic, add_rss_feed, remove_rss_feed, set_rss_feed_options,
            # AI/FAQ
            get_ai_settings, set_ai_settings, upsert_faq, delete_faq,
            # Daily stats
            set_daily_stats,
            # Mood
            set_mood_question, set_mood_topic,
            # Language
            set_group_language,
            # Night mode
            set_night_mode,
            # Topic router
            add_topic_router_rule,
        )
    except Exception:
        from .database import (
            get_registered_groups,
            set_welcome, delete_welcome, get_welcome,
            set_rules, delete_rules, get_rules,
            set_farewell, delete_farewell, get_farewell,
            get_link_settings, set_link_settings, set_spam_policy_topic,
            set_rss_topic, get_rss_topic, add_rss_feed, remove_rss_feed, set_rss_feed_options,
            get_ai_settings, set_ai_settings, upsert_faq, delete_faq,
            set_daily_stats,
            set_mood_question, set_mood_topic,
            set_group_language,
            set_night_mode,
            add_topic_router_rule,
        )
    return locals()

# --- Helpers -----------------------------------------------------------------
async def _is_admin_or_owner(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        cm = await context.bot.get_chat_member(chat_id, user_id)
        return (getattr(cm, "status", "") or "").lower() in ("administrator", "creator")
    except Exception as e:
        logger.debug(f"[miniapp] get_chat_member({chat_id},{user_id}) failed: {e}")
        return False

def _webapp_url(cid: int, title: Optional[str]) -> str:
    return f"{MINIAPP_URL}?cid={cid}&title={urllib.parse.quote(title or str(cid))}"

def _hm_to_min(hhmm: str, default_min: int) -> int:
    try:
        hh, mm = (hhmm or "").split(":")
        return int(hh)*60 + int(mm)
    except Exception:
        return default_min

# --- /miniapp: nur im Privatchat und NUR Admin-Gruppen -----------------------
async def miniapp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat and update.effective_chat.type != "private":
        return
    if not update.effective_user or not update.effective_message:
        return
    user = update.effective_user
    msg  = update.effective_message

    db = _db()
    try:
        all_groups: List[Tuple[int, str]] = db["get_registered_groups"]() or []
    except Exception as e:
        logger.warning(f"[miniapp] get_registered_groups failed: {e}")
        all_groups = []

    rows: List[List[InlineKeyboardButton]] = []
    for cid, title in all_groups:
        try:
            cid = int(cid)
        except Exception:
            continue
        if not await _is_admin_or_owner(context, cid, user.id):
            continue
        rows.append([InlineKeyboardButton(f"{title or cid} – Mini-App öffnen", web_app=WebAppInfo(url=_webapp_url(cid, title)))])
    if not rows:
        return await msg.reply_text("❌ Du bist in keiner registrierten Gruppe Admin/Owner.")
    await msg.reply_text("Wähle eine Gruppe:", reply_markup=InlineKeyboardMarkup(rows))

# --- WebApp-Daten speichern (Admin-Check serverseitig) ------------------------
async def webapp_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not getattr(msg, "web_app_data", None):
        return

    try:
        data = json.loads(msg.web_app_data.data or "{}")
    except Exception:
        return await msg.reply_text("❌ Ungültige Daten von der Mini-App.")

    try:
        cid = int(data.get("cid"))
    except Exception:
        return await msg.reply_text("❌ Gruppen-ID fehlt oder ist ungültig.")

    # Nur Admin/Owner dürfen speichern
    if not await _is_admin_or_owner(context, cid, update.effective_user.id):
        return await msg.reply_text("❌ Du bist in dieser Gruppe kein Admin.")

    db = _db()
    errors: List[str] = []

    # --- Welcome / Farewell / Rules -----------------------------------------
    try:
        if data.get("welcome_on"):
            text = (data.get("welcome_text") or "Willkommen {user} 👋").strip()
            db["set_welcome"](cid, None, text)  # (chat_id, photo_id, text) :contentReference[oaicite:1]{index=1}
        else:
            db["delete_welcome"](cid)
    except Exception as e:
        errors.append(f"Welcome: {e}")

    try:
        if data.get("farewell_on"):
            db["set_farewell"](cid, None, (data.get("farewell_text") or "Tschüss {user}!").strip())
        else:
            db["delete_farewell"](cid)
    except Exception as e:
        errors.append(f"Farewell: {e}")

    try:
        if data.get("rules_on") and (data.get("rules_text") or "").strip():
            db["set_rules"](cid, None, data["rules_text"].strip())  # (chat_id, photo_id, text) :contentReference[oaicite:2]{index=2}
        else:
            db["delete_rules"](cid)
    except Exception as e:
        errors.append(f"Regeln: {e}")

    # --- Clean Deleted (nur Flag; Zeiten/Weekday nicht im UI) ---------------
    try:
        if "clean_deleted" in data:
            db["set_clean_deleted_settings"](cid, enabled=bool(data.get("clean_deleted")))
    except Exception as e:
        errors.append(f"Aufräumen: {e}")

    # --- Links/Spam: Gruppen-Flags + (optional) Topic-Overrides -------------
    try:
        db["set_link_settings"](
            cid,
            admins_only=bool(data.get("links_admins_only")),
            # optionale Warnung/Text aus UI ergänzbar:
            # warning_on=..., warning_text=..., exceptions_on=...
        )  # schreibt group_settings (admins_only==link_protection_enabled) :contentReference[oaicite:3]{index=3}
    except Exception as e:
        errors.append(f"Links: {e}")

    wl = [x.strip() for x in (data.get("whitelist") or "").splitlines() if x.strip()]
    bl = [x.strip() for x in (data.get("blacklist") or "").splitlines() if x.strip()]
    topic_id_raw = (data.get("topic_id") or "").strip()
    if topic_id_raw.isdigit():
        try:
            db["set_spam_policy_topic"](
                cid, int(topic_id_raw),
                link_whitelist=wl or None,
                domain_blacklist=bl or None
            )  # Topic-Override-Setter :contentReference[oaicite:4]{index=4}
        except Exception as e:
            errors.append(f"Spam Topic {topic_id_raw}: {e}")

    # --- RSS: Topic + Feeds + Optionen pro Feed -----------------------------
    try:
        # wir nutzen vorhandenes RSS-Topic (oder 0)
        rss_topic = 0
        try:
            rss_topic = db["get_rss_topic"](cid) or 0
        except Exception:
            pass

        post_images = bool(data.get("rss_images"))

        # Feeds hinzufügen
        add_list = [u.strip() for u in (data.get("rss_add") or "").splitlines() if u.strip()]
        for url in add_list:
            try:
                db["add_rss_feed"](cid, url, rss_topic)
                db["set_rss_feed_options"](cid, url, post_images=post_images)
            except Exception as e:
                errors.append(f"RSS add {url}: {e}")

        # Feeds entfernen
        del_list = [u.strip() for u in (data.get("rss_del") or "").splitlines() if u.strip()]
        for url in del_list:
            try:
                db["remove_rss_feed"](cid, url)
            except Exception as e:
                errors.append(f"RSS del {url}: {e}")
    except Exception as e:
        errors.append(f"RSS: {e}")

    # --- AI / FAQ ------------------------------------------------------------
    try:
        db["set_ai_settings"](cid, faq=bool(data.get("ai_faq")), rss=bool(data.get("ai_rss")))
    except Exception as e:
        errors.append(f"KI: {e}")

    faq_add = data.get("faq_add") or None
    if faq_add and (faq_add.get("q") or "").strip():
        try:
            db["upsert_faq"](cid, faq_add["q"].strip(), (faq_add.get("a") or "").strip())
        except Exception as e:
            errors.append(f"FAQ add: {e}")
    faq_del = data.get("faq_del") or None
    if faq_del and (faq_del.get("q") or "").strip():
        try:
            db["delete_faq"](cid, faq_del["q"].strip())
        except Exception as e:
            errors.append(f"FAQ del: {e}")

    # --- Daily Stats ---------------------------------------------------------
    try:
        if "daily_stats" in data:
            db["set_daily_stats"](cid, bool(data.get("daily_stats")))
    except Exception as e:
        errors.append(f"Report: {e}")

    # --- Mood ----------------------------------------------------------------
    try:
        if (data.get("mood_question") or "").strip():
            db["set_mood_question"](cid, data["mood_question"].strip())
        if (data.get("mood_topic") or "").strip().isdigit():
            db["set_mood_topic"](cid, int(data["mood_topic"].strip()))
    except Exception as e:
        errors.append(f"Mood: {e}")

    # --- Sprache -------------------------------------------------------------
    try:
        if (data.get("language") or "").strip():
            db["set_group_language"](cid, data["language"].strip()[:5])
    except Exception as e:
        errors.append(f"Sprache: {e}")

    # --- Nachtmodus ----------------------------------------------------------
    try:
        night = data.get("night") or {}
        if "on" in night or "start" in night or "end" in night:
            enabled = bool(night.get("on"))
            start_m = _hm_to_min(night.get("start") or "22:00", 1320)
            end_m   = _hm_to_min(night.get("end") or "07:00", 360)
            db["set_night_mode"](cid, enabled=enabled, start_minute=start_m, end_minute=end_m)
            # days wird aktuell nicht vom Schema unterstützt
    except Exception as e:
        errors.append(f"Nachtmodus: {e}")

    # --- Topic-Router (einfache „pattern → topicId“-Zeilen) ------------------
    try:
        rule_text = (data.get("router_rule") or "").strip()
        if rule_text:
            for line in rule_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "→" in line:
                    patt, tid = [x.strip() for x in line.split("→", 1)]
                elif "->" in line:
                    patt, tid = [x.strip() for x in line.split("->", 1)]
                else:
                    continue
                if not patt or not tid.lstrip("-").isdigit():
                    continue
                db["add_topic_router_rule"](cid, int(tid), keywords=[patt])
    except Exception as e:
        errors.append(f"Router: {e}")

    if errors:
        return await msg.reply_text("⚠️ Teilweise gespeichert:\n• " + "\n• ".join(errors))
    return await msg.reply_text("✅ Einstellungen gespeichert.")

# --- Registrierung ------------------------------------------------------------
def register_miniapp(app: Application):
    app.add_handler(CommandHandler("miniapp", miniapp_cmd, filters=filters.ChatType.PRIVATE), group=-3)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, webapp_data_handler), group=0)
    logger.info("miniapp: handlers registered")