import os, json, urllib.parse, logging
from typing import List, Tuple
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
)
from telegram.ext import (
    CommandHandler, MessageHandler, ContextTypes, filters, Application
)

logger = logging.getLogger(__name__)

# URL deiner gehosteten index.html (Canvas-Version) – per ENV überschreibbar
MINIAPP_URL = os.getenv(
    "MINIAPP_URL",
    "https://greeny187.github.io/GreenyManagementBots/index.html"
)

# --- DB-Fallback-Imports ------------------------------------------------------
def _db():
    """Liefert die DB-Funktionen, egal ob shared.* vorhanden ist oder lokale .database."""
    try:
        from shared.database import (
            get_registered_groups,
            set_welcome, delete_welcome,
            get_link_settings, set_link_settings,
            get_ai_settings, set_ai_settings,
        )
    except Exception:
        from shared.database import (
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

# --- Helper -------------------------------------------------------------------
async def _is_admin_or_owner(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    """True, wenn der Nutzer in chat_id Admin/Owner ist (ein get_chat_member-Call)."""
    try:
        cm = await context.bot.get_chat_member(chat_id, user_id)
        status = (getattr(cm, "status", "") or "").lower()
        return status in ("administrator", "creator")
    except Exception as e:
        logger.debug(f"[miniapp] get_chat_member({chat_id},{user_id}) failed: {e}")
        return False

# --- /miniapp Befehl ----------------------------------------------------------
async def miniapp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt Buttons, um die Mini-App pro Gruppe zu öffnen."""
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

    visible: List[Tuple[int, str]] = []
    for cid, title in all_groups:
        if not isinstance(cid, int):
            # falls DB (cid, title) anders liefert
            try:
                cid = int(cid)
            except Exception:
                continue
        if await _is_admin_or_owner(context, cid, user.id):
            visible.append((cid, title))

    if not visible:
        return await msg.reply_text("Keine Gruppe gefunden, in der du Admin bist.")

    rows = []
    for cid, title in visible:
        url = f"{MINIAPP_URL}?cid={cid}&title={urllib.parse.quote(title or str(cid))}"
        rows.append([InlineKeyboardButton(f"{title or cid} – Mini-App öffnen", web_app=WebAppInfo(url=url))])

    await msg.reply_text("Wähle eine Gruppe:", reply_markup=InlineKeyboardMarkup(rows))

# --- Rückkanal der Mini-App ---------------------------------------------------
async def webapp_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not getattr(msg, "web_app_data", None):
        return

    try:
        data = json.loads(msg.web_app_data.data or "{}")
    except Exception:
        return await msg.reply_text("❌ Ungültige Daten von der Mini-App.")

    # --- Basics ---
    cid_raw = data.get("cid")
    try:
        cid = int(cid_raw)
    except Exception:
        return await msg.reply_text("❌ Gruppen-ID fehlt oder ist ungültig.")

    # Sicherheitscheck: Absender muss Admin/Owner in der Zielgruppe sein
    if not await _is_admin_or_owner(context, cid, update.effective_user.id):
        return await msg.reply_text("❌ Du bist in dieser Gruppe kein Admin.")

    db = _db()
    errors = []

    # --- Begrüßung / Abschied ---
    try:
        if data.get("welcome_on"):
            db["set_welcome"](cid, (data.get("welcome_text") or "Willkommen {user} 👋").strip())
        else:
            db["delete_welcome"](cid)
    except Exception as e:
        errors.append(f"Welcome: {e}")

    try:
        # Farewell optional: gleiche Funktion wie Welcome, sofern vorhanden
        from shared.database import set_farewell, delete_farewell  # prefer shared
    except Exception:
        try:
            from shared.database import set_farewell, delete_farewell
        except Exception:
            set_farewell = delete_farewell = None
    if set_farewell and delete_farewell:
        try:
            if data.get("farewell_on"):
                set_farewell(cid, (data.get("farewell_text") or "Tschüss {user}!").strip())
            else:
                delete_farewell(cid)
        except Exception as e:
            errors.append(f"Farewell: {e}")

    # --- Regeln & Clean Deleted ---
    try:
        from shared.database import set_rules, delete_rules, set_clean_deleted_settings
    except Exception:
        try:
            from shared.database import set_rules, delete_rules, set_clean_deleted_settings
        except Exception:
            set_rules = delete_rules = set_clean_deleted_settings = None
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
        # grobe Felder; dein DB-Schema kann mehr können – wir lassen Unbekanntes unangetastet
        cfg["spam_level"] = data.get("spam_level", "mid")
        cfg["admins_only"] = bool(data.get("links_admins_only"))
        # Whitelist/Blacklist als Listen
        wl = [x.strip() for x in (data.get("whitelist") or "").splitlines() if x.strip()]
        bl = [x.strip() for x in (data.get("blacklist") or "").splitlines() if x.strip()]
        if wl: cfg["whitelist"] = wl
        if bl: cfg["blacklist"] = bl
        db["set_link_settings"](cid, cfg)
    except Exception as e:
        errors.append(f"Spam/Links: {e}")

    # Optional: Topic-spezifisch (wenn deine DB-API das kann)
    topic_id = (data.get("topic_id") or "").strip()
    if topic_id.isdigit():
        try:
            from shared.database import set_spam_policy_topic
        except Exception:
            try:
                from shared.database import set_spam_policy_topic
            except Exception:
                set_spam_policy_topic = None
        if set_spam_policy_topic:
            try:
                set_spam_policy_topic(cid, int(topic_id), {"whitelist": wl, "blacklist": bl})
            except Exception as e:
                errors.append(f"Spam Topic {topic_id}: {e}")

    # --- RSS ---
    try:
        from shared.database import add_rss_feed, remove_rss_feed, set_rss_feed_options
    except Exception:
        try:
            from shared.database import add_rss_feed, remove_rss_feed, set_rss_feed_options
        except Exception:
            add_rss_feed = remove_rss_feed = set_rss_feed_options = None
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
        errors.append(f\"KI: {e}\")

    # --- FAQ Verwaltung ---
    try:
        from shared.database import upsert_faq, delete_faq
    except Exception:
        try:
            from .database import upsert_faq, delete_faq
        except Exception:
            upsert_faq = delete_faq = None
    faq_add = data.get(\"faq_add\") or None
    if upsert_faq and faq_add and (faq_add.get(\"q\") or \"\").strip():
        try:
            upsert_faq(cid, faq_add[\"q\"].strip(), (faq_add.get(\"a\") or \"\").strip())
        except Exception as e:
            errors.append(f\"FAQ add: {e}\")
    faq_del = data.get(\"faq_del\") or None
    if delete_faq and faq_del and (faq_del.get(\"q\") or \"\").strip():
        try:
            delete_faq(cid, faq_del[\"q\"].strip())
        except Exception as e:
            errors.append(f\"FAQ del: {e}\")

    # --- Report / Statistiken ---
    try:
        from shared.database import set_daily_stats
    except Exception:
        try:
            from .database import set_daily_stats
        except Exception:
            set_daily_stats = None
    if set_daily_stats:
        try:
            set_daily_stats(cid, bool(data.get(\"daily_stats\")))
        except Exception as e:
            errors.append(f\"Report: {e}\")

    # --- Mood ---
    try:
        from shared.database import set_mood_question, set_mood_topic
    except Exception:
        try:
            from .database import set_mood_question, set_mood_topic
        except Exception:
            set_mood_question = set_mood_topic = None
    if set_mood_question:
        try:
            if (data.get(\"mood_question\") or \"\").strip(): set_mood_question(cid, data[\"mood_question\"].strip())
            if (data.get(\"mood_topic\") or \"\").strip().isdigit(): set_mood_topic(cid, int(data[\"mood_topic\"].strip()))
        except Exception as e:
            errors.append(f\"Mood: {e}\")

    # --- Sprache ---
    try:
        from shared.database import set_group_language
    except Exception:
        try:
            from .database import set_group_language
        except Exception:
            set_group_language = None
    if set_group_language:
        try:
            lang = (data.get(\"language\") or \"de\").strip()[:5]
            set_group_language(cid, lang)
        except Exception as e:
            errors.append(f\"Sprache: {e}\")

    # --- Nachtmodus ---
    try:
        from shared.database import set_night_mode
    except Exception:
        try:
            from .database import set_night_mode
        except Exception:
            set_night_mode = None
    night = data.get(\"night\") or {}
    if set_night_mode:
        try:
            nm = {\n                \"enabled\": bool(night.get(\"on\")),\n                \"start\": (night.get(\"start\") or \"22:00\"),\n                \"end\":   (night.get(\"end\")   or \"07:00\"),\n                \"days\":  (night.get(\"days\")  or \"\").strip(),\n            }\n            set_night_mode(cid, nm)\n        except Exception as e:\n            errors.append(f\"Nachtmodus: {e}\")\n\n    # --- Router-Regel (einfaches Format \"pattern → topic\") ---\n    try:\n        from shared.database import add_topic_router_rule\n    except Exception:\n        try:\n            from .database import add_topic_router_rule\n        except Exception:\n            add_topic_router_rule = None\n    if add_topic_router_rule:\n        try:\n            rule = (data.get(\"router_rule\") or \"\").strip()\n            if rule:\n                for line in rule.splitlines():\n                    if \"→\" in line:\n                        patt, tid = [x.strip() for x in line.split(\"→\",1)]\n                    elif \"->\" in line:\n                        patt, tid = [x.strip() for x in line.split(\"->\",1)]\n                    else:\n                        continue\n                    if patt and tid.lstrip(\"-\").isdigit():\n                        add_topic_router_rule(cid, patt, int(tid))\n        except Exception as e:\n            errors.append(f\"Router: {e}\")\n\n    # --- Antwort ---\n    if errors:\n        return await msg.reply_text(\"⚠️ Teilweise gespeichert:\\n• \" + \"\\n• \".join(errors))\n    return await msg.reply_text(\"✅ Einstellungen gespeichert.\")\n```


# --- Öffentliche Registrierung ------------------------------------------------
def register_miniapp(app: Application):
    """Von app.register(...) oder deiner main.py aufrufen."""
    # /miniapp sehr früh, damit Nutzer sie leicht finden
    app.add_handler(CommandHandler("miniapp", miniapp_cmd), group=-3)

    # WebApp-Daten kommen als Message im Privat-Chat.
    # Wir filtern locker und prüfen im Handler selbst auf msg.web_app_data.
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, webapp_data_handler), group=0)

    logger.info("miniapp: handlers registered")