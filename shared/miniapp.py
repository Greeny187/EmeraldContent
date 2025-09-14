import os, json, urllib.parse, logging
from typing import List, Tuple, Optional
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
)
from telegram.ext import (
    CommandHandler, MessageHandler, ContextTypes, filters, Application
)

logger = logging.getLogger(__name__)

# URL deiner gehosteten index.html – per ENV überschreibbar
MINIAPP_URL = os.getenv(
    "MINIAPP_URL",
    "https://greeny187.github.io/EmeraldContentBots/miniapp/index.html"
)

# --- DB-Brücke ---------------------------------------------------------------
def _db():
    """Liefert DB-Funktionen – shared.* bevorzugt, sonst lokale .database."""
    try:
        from shared.database import (
            get_registered_groups,
            set_welcome, delete_welcome,
            get_link_settings, set_link_settings,
            get_ai_settings, set_ai_settings,
        )
    except Exception:
        from .database import (
            get_registered_groups,
            set_welcome, delete_welcome,
            get_link_settings, set_link_settings,
            get_ai_settings, set_ai_settings,
        )
    return {
        "get_registered_groups": get_registered_groups,
        "set_welcome": set_welcome, "delete_welcome": delete_welcome,
        "get_link_settings": get_link_settings, "set_link_settings": set_link_settings,
        "get_ai_settings": get_ai_settings, "set_ai_settings": set_ai_settings,
    }

# --- Helpers -----------------------------------------------------------------
async def _is_admin_or_owner(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    """True, wenn user_id in chat_id Admin/Owner ist (ein get_chat_member-Call)."""
    try:
        cm = await context.bot.get_chat_member(chat_id, user_id)
        status = (getattr(cm, "status", "") or "").lower()
        return status in ("administrator", "creator")
    except Exception as e:
        logger.debug(f"[miniapp] get_chat_member({chat_id},{user_id}) failed: {e}")
        return False

def _webapp_url(cid: int, title: Optional[str]) -> str:
    return f"{MINIAPP_URL}?cid={cid}&title={urllib.parse.quote(title or str(cid))}"

# --- /miniapp Befehl ---------------------------------------------------------
async def miniapp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt die Mini-App nur für Gruppen, in denen der Aufrufer Admin/Owner ist."""
    # Nur im Privatchat
    if update.effective_chat and update.effective_chat.type != "private":
        return  # leise ignorieren oder: await update.message.reply_text("Bitte im Privatchat nutzen.")

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

    # Nur Gruppen, in denen der Nutzer Admin/Owner ist
    rows: List[List[InlineKeyboardButton]] = []
    for cid, title in all_groups:
        try:
            cid = int(cid)
        except Exception:
            continue
        if not await _is_admin_or_owner(context, cid, user.id):
            continue
        url = _webapp_url(cid, title)
        rows.append([InlineKeyboardButton(f"{title or cid} – Mini-App öffnen", web_app=WebAppInfo(url=url))])

    if not rows:
        return await msg.reply_text("❌ Du bist in keiner registrierten Gruppe Admin/Owner.")

    await msg.reply_text("Wähle eine Gruppe:", reply_markup=InlineKeyboardMarkup(rows))

# --- Rückkanal der Mini-App --------------------------------------------------
async def webapp_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Empfängt JSON von der Mini-App (update.message.web_app_data.data) und speichert Settings.
    Alle Abschnitte sind robust gegen fehlende DB-Funktionen (try/except).
    """
    msg = update.message
    if not msg or not getattr(msg, "web_app_data", None):
        return

    # JSON parsen
    try:
        data = json.loads(msg.web_app_data.data or "{}")
    except Exception:
        return await msg.reply_text("❌ Ungültige Daten von der Mini-App.")

    # Gruppen-ID
    try:
        cid = int(data.get("cid"))
    except Exception:
        return await msg.reply_text("❌ Gruppen-ID fehlt oder ist ungültig.")

    # Sicherheitscheck: Absender muss Admin/Owner in der Zielgruppe sein
    if not await _is_admin_or_owner(context, cid, update.effective_user.id):
        return await msg.reply_text("❌ Du bist in dieser Gruppe kein Admin.")

    db = _db()
    errors: List[str] = []

    # --- Begrüßung / Abschied ---
    try:
        if data.get("welcome_on"):
            text = (data.get("welcome_text") or "Willkommen {user} 👋").strip()
            db["set_welcome"](cid, text)
        else:
            db["delete_welcome"](cid)
    except Exception as e:
        errors.append(f"Welcome: {e}")

    # Farewell
    set_farewell = delete_farewell = None
    try:
        from shared.database import set_farewell, delete_farewell
    except Exception:
        try:
            from .database import set_farewell, delete_farewell
        except Exception:
            pass
    if set_farewell and delete_farewell:
        try:
            if data.get("farewell_on"):
                set_farewell(cid, (data.get("farewell_text") or "Tschüss {user}!").strip())
            else:
                delete_farewell(cid)
        except Exception as e:
            errors.append(f"Farewell: {e}")

    # --- Regeln & Clean Deleted ---
    set_rules = delete_rules = set_clean_deleted_settings = None
    try:
        from shared.database import set_rules, delete_rules, set_clean_deleted_settings
    except Exception:
        try:
            from .database import set_rules, delete_rules, set_clean_deleted_settings
        except Exception:
            pass
    if set_rules and delete_rules:
        try:
            if data.get("rules_on") and (data.get("rules_text") or "").strip():
                set_rules(cid, data["rules_text"].strip())
            else:
                delete_rules(cid)
        except Exception as e:
            errors.append(f"Regeln: {e}")
    if set_clean_deleted_settings:
        try:
            set_clean_deleted_settings(cid, bool(data.get("clean_deleted")))
        except Exception as e:
            errors.append(f"Aufräumen: {e}")

    # --- Spam / Links ---
    try:
        cfg = db["get_link_settings"](cid) or {}
        cfg["spam_level"] = data.get("spam_level", "mid")
        cfg["admins_only"] = bool(data.get("links_admins_only"))
        wl = [x.strip() for x in (data.get("whitelist") or "").splitlines() if x.strip()]
        bl = [x.strip() for x in (data.get("blacklist") or "").splitlines() if x.strip()]
        if wl: cfg["whitelist"] = wl
        if bl: cfg["blacklist"] = bl
        db["set_link_settings"](cid, cfg)
    except Exception as e:
        errors.append(f"Spam/Links: {e}")

    # Topic-spezifische Listen (falls vorhanden)
    topic_id = (data.get("topic_id") or "").strip()
    if topic_id.isdigit():
        set_spam_policy_topic = None
        try:
            from shared.database import set_spam_policy_topic
        except Exception:
            try:
                from .database import set_spam_policy_topic
            except Exception:
                pass
        if set_spam_policy_topic:
            try:
                set_spam_policy_topic(cid, int(topic_id), {"whitelist": wl, "blacklist": bl})
            except Exception as e:
                errors.append(f"Spam Topic {topic_id}: {e}")

    # --- RSS ---
    add_rss_feed = remove_rss_feed = set_rss_feed_options = None
    try:
        from shared.database import add_rss_feed, remove_rss_feed, set_rss_feed_options
    except Exception:
        try:
            from .database import add_rss_feed, remove_rss_feed, set_rss_feed_options
        except Exception:
            pass
    if set_rss_feed_options:
        try:
            set_rss_feed_options(cid, {"images": bool(data.get("rss_images"))})
        except Exception as e:
            errors.append(f"RSS-Optionen: {e}")
    if add_rss_feed:
        try:
            for url in [u.strip() for u in (data.get("rss_add") or "").splitlines() if u.strip()]:
                add_rss_feed(cid, url)
        except Exception as e:
            errors.append(f"RSS hinzufügen: {e}")
    if remove_rss_feed:
        try:
            for url in [u.strip() for u in (data.get("rss_del") or "").splitlines() if u.strip()]:
                remove_rss_feed(cid, url)
        except Exception as e:
            errors.append(f"RSS entfernen: {e}")

    # --- KI ---
    try:
        ai_faq_old, ai_rss_old = db["get_ai_settings"](cid)
        db["set_ai_settings"](cid, bool(data.get("ai_faq")), bool(data.get("ai_rss")))
    except Exception as e:
        errors.append(f"KI: {e}")

    # --- FAQ Verwaltung ---
    upsert_faq = delete_faq = None
    try:
        from shared.database import upsert_faq, delete_faq
    except Exception:
        try:
            from .database import upsert_faq, delete_faq
        except Exception:
            pass
    faq_add = data.get("faq_add") or None
    if upsert_faq and faq_add and (faq_add.get("q") or "").strip():
        try:
            upsert_faq(cid, faq_add["q"].strip(), (faq_add.get("a") or "").strip())
        except Exception as e:
            errors.append(f"FAQ add: {e}")
    faq_del = data.get("faq_del") or None
    if delete_faq and faq_del and (faq_del.get("q") or "").strip():
        try:
            delete_faq(cid, faq_del["q"].strip())
        except Exception as e:
            errors.append(f"FAQ del: {e}")

    # --- Report / Statistiken ---
    set_daily_stats = None
    try:
        from shared.database import set_daily_stats
    except Exception:
        try:
            from .database import set_daily_stats
        except Exception:
            pass
    if set_daily_stats:
        try:
            set_daily_stats(cid, bool(data.get("daily_stats")))
        except Exception as e:
            errors.append(f"Report: {e}")

    # --- Mood ---
    set_mood_question = set_mood_topic = None
    try:
        from shared.database import set_mood_question, set_mood_topic
    except Exception:
        try:
            from .database import set_mood_question, set_mood_topic
        except Exception:
            pass
    if set_mood_question:
        try:
            if (data.get("mood_question") or "").strip():
                set_mood_question(cid, data["mood_question"].strip())
            if (data.get("mood_topic") or "").strip().isdigit():
                set_mood_topic(cid, int(data["mood_topic"].strip()))
        except Exception as e:
            errors.append(f"Mood: {e}")

    # --- Sprache ---
    set_group_language = None
    try:
        from shared.database import set_group_language
    except Exception:
        try:
            from .database import set_group_language
        except Exception:
            pass
    if set_group_language:
        try:
            lang = (data.get("language") or "de").strip()[:5]
            set_group_language(cid, lang)
        except Exception as e:
            errors.append(f"Sprache: {e}")

    # --- Nachtmodus ---
    set_night_mode = None
    try:
        from shared.database import set_night_mode
    except Exception:
        try:
            from .database import set_night_mode
        except Exception:
            pass
    night = data.get("night") or {}
    if set_night_mode:
        try:
            nm = {
                "enabled": bool(night.get("on")),
                "start": (night.get("start") or "22:00"),
                "end":   (night.get("end")   or "07:00"),
                "days":  (night.get("days")  or "").strip(),
            }
            set_night_mode(cid, nm)
        except Exception as e:
            errors.append(f"Nachtmodus: {e}")

    # --- Router-Regel ---
    add_topic_router_rule = None
    try:
        from shared.database import add_topic_router_rule
    except Exception:
        try:
            from .database import add_topic_router_rule
        except Exception:
            pass
    rule_text = (data.get("router_rule") or "").strip()
    if add_topic_router_rule and rule_text:
        try:
            for line in rule_text.splitlines():
                if "→" in line:
                    patt, tid = [x.strip() for x in line.split("→", 1)]
                elif "->" in line:
                    patt, tid = [x.strip() for x in line.split("->", 1)]
                else:
                    continue
                if patt and tid.lstrip("-").isdigit():
                    add_topic_router_rule(cid, patt, int(tid))
        except Exception as e:
            errors.append(f"Router: {e}")

    # --- Antwort ---
    if errors:
        return await msg.reply_text("⚠️ Teilweise gespeichert:\n• " + "\n• ".join(errors))
    return await msg.reply_text("✅ Einstellungen gespeichert.")

# --- Öffentliche Registrierung ------------------------------------------------
def register_miniapp(app: Application):
    # /miniapp nur im Privatchat
    app.add_handler(CommandHandler("miniapp", miniapp_cmd, filters=filters.ChatType.PRIVATE), group=-3)

    # WebApp-Daten kommen im Privatchat – Handler darf breit filtern;
    # im Code prüfen wir zusätzlich auf msg.web_app_data.
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, webapp_data_handler), group=0)

    logger.info("miniapp: handlers registered")