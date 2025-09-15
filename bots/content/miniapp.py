import os
import json
import urllib.parse
import logging
from io import BytesIO
from aiohttp.web_response import Response
from typing import List, Tuple, Optional
from datetime import date, timedelta
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logger = logging.getLogger(__name__)

# === Konfiguration ============================================================
MINIAPP_URL = os.getenv(
    "MINIAPP_URL",
    # dein neuer Pfad:
    "https://greeny187.github.io/EmeraldContentBots/miniapp/appcontent.html"
)
MINIAPP_API_BASE = os.getenv("MINIAPP_API_BASE", "").rstrip("/")

# Erlaubter Origin für CORS (aus MINIAPP_URL abgeleitet)
def _origin(url: str) -> str:
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return "*"

ALLOWED_ORIGIN = _origin(MINIAPP_URL)

# === DB-Brücke: Funktionen dynamisch laden (shared.database ODER lokale database) ===
def _db():
    try:
        # bevorzugt shared
        from shared.database import (
            # Gruppen
            get_registered_groups,
            # Welcome/Rules/Farewell
            set_welcome, delete_welcome, get_welcome,
            set_rules, delete_rules, get_rules,
            set_farewell, delete_farewell, get_farewell,
            # Links/Spam global & Topic-Overrides
            get_link_settings, set_link_settings, set_spam_policy_topic,
            # RSS
            set_rss_topic, get_rss_topic, add_rss_feed, remove_rss_feed, set_rss_feed_options, list_rss_feeds,
            # AI/FAQ
            get_ai_settings, set_ai_settings, upsert_faq, delete_faq,
            # Daily stats
            set_daily_stats, is_daily_stats_enabled, get_top_responders, get_agg_rows,
            # Mood
            set_mood_question, get_mood_question, set_mood_topic, get_mood_topic,
            # Language
            set_group_language,
            # Night mode
            set_night_mode,
            # Topic router
            add_topic_router_rule,
        )
    except Exception:
        # fallback: lokale DB
        from .database import (
            get_registered_groups,
            set_welcome, delete_welcome, get_welcome,
            set_rules, delete_rules, get_rules,
            set_farewell, delete_farewell, get_farewell,
            get_link_settings, set_link_settings, set_spam_policy_topic,
            set_rss_topic, get_rss_topic, add_rss_feed, remove_rss_feed, set_rss_feed_options, list_rss_feeds,
            get_ai_settings, set_ai_settings, upsert_faq, delete_faq,
            set_daily_stats, is_daily_stats_enabled, get_top_responders, get_agg_rows,
            set_mood_question, get_mood_question, set_mood_topic, get_mood_topic,
            set_group_language,
            set_night_mode,
            add_topic_router_rule,
        )
    return locals()

# === Helpers =================================================================
async def _is_admin_or_owner(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        cm = await context.bot.get_chat_member(chat_id, user_id)
        status = (getattr(cm, "status", "") or "").lower()
        return status in ("administrator", "creator")
    except Exception as e:
        logger.debug(f"[miniapp] get_chat_member({chat_id},{user_id}) failed: {e}")
        return False

async def _is_admin(app: Application, cid: int, uid: int) -> bool:
    try:
        member = await app.bot.get_chat_member(cid, uid)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False

def _webapp_url(cid: int, title: Optional[str]) -> str:
    url = f"{MINIAPP_URL}?cid={cid}&title={urllib.parse.quote(title or str(cid))}"
    if MINIAPP_API_BASE:
        url += f"&api={urllib.parse.quote(MINIAPP_API_BASE)}"
    return url

def _hm_to_min(hhmm: str, default_min: int) -> int:
    try:
        hh, mm = (hhmm or "").split(":")
        return int(hh) * 60 + int(mm)
    except Exception:
        return default_min

def _cors_json(data: dict, status: int = 200):
    return web.json_response(
        data, status=status,
        headers={
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
        }
    )

async def _file_proxy(request: web.Request) -> Response:
    app: Application = request.app["ptb_app"]
    if request.method == "OPTIONS":
        return _cors_json({})
    try:
        cid = int(request.query.get("cid","0"))
        uid = int(request.query.get("uid","0"))
        fid = (request.query.get("file_id") or "").strip()
    except Exception:
        return _cors_json({"error":"bad_params"}, 400)
    if not fid:
        return _cors_json({"error":"missing_file_id"}, 400)
    if not await _is_admin(app, cid, uid):
        return _cors_json({"error":"forbidden"}, 403)

    try:
        f = await app.bot.get_file(fid)
        buf = BytesIO()
        await f.download_to_memory(out=buf)
        buf.seek(0)
        # Content-Type rudimentär (optional verbessern über Dateiendung)
        return Response(
            body=buf.read(),
            headers={"Access-Control-Allow-Origin": ALLOWED_ORIGIN},
            content_type="application/octet-stream"
        )
    except Exception as e:
        logger.warning(f"[miniapp] file proxy failed: {e}")
        return _cors_json({"error":"not_found"}, 404)
    

# --- kleine Helferblöcke (DB-Aufrufe sauber gekapselt) -----------------------
def _mk_media_block(cid:int, kind:str):
    # kind ∈ {"welcome","rules","farewell"}
    db = _db()
    loader = {"welcome": "get_welcome", "rules": "get_rules", "farewell":"get_farewell"}[kind]
    ph, tx = (None, None)
    try:
        r = db[loader](cid)
        if r:
            ph, tx = r
    except Exception:
        pass
    return {
        "on": bool(tx),
        "text": tx or "",
        "photo": bool(ph),
        # relative Proxy-URL (Client hängt cid/uid an)
        "photo_id": ph or ""
    }

async def _state_json(cid: int) -> dict:
    db = _db()
    link = db["get_link_settings"](cid) or {}  # only_admin_links, warning_enabled, warning_text, exceptions_enabled  # noqa
    ai = db["get_ai_settings"](cid) or (False, False)
    ai_faq, ai_rss = ai

    rss_topic = None
    feeds = []
    try:
        rss_topic = db["get_rss_topic"](cid)
        feeds = [{"url": u, "topic": t} for (u, t) in (db["list_rss_feeds"](cid) or [])]
    except Exception:
        pass
    sub = {}
    try:
        sub = db["get_subscription_info"](cid) or {}
    except Exception:
        sub = {"tier":"free", "active":False, "valid_until":None}

    return {
        "welcome": _mk_media_block(cid, "welcome"),
        "rules":   _mk_media_block(cid, "rules"),
        "farewell":_mk_media_block(cid, "farewell"),
        "links": {...},
        "ai": {...},
        "mood": {...},
        "rss": {...},
        "daily_stats": db["is_daily_stats_enabled"](cid),
        "subscription": sub,  # <-- wichtig für Pro/Free im Frontend
    }

# === HTTP-Routen (nur lesend, Admin-Gate per Bot) ============================
async def route_state(request: web.Request):
    app: Application = request.app["ptb_app"]
    if request.method == "OPTIONS":
        return _cors_json({})
    try:
        cid = int(request.query.get("cid", "0"))
        uid = int(request.query.get("uid", "0"))
    except Exception:
        return _cors_json({"error": "bad_params"}, 400)

    if not await _is_admin(app, cid, uid):
        return _cors_json({"error": "forbidden"}, 403)

    return _cors_json(await _state_json(cid))

async def route_stats(request: web.Request):
    app: Application = request.app["ptb_app"]
    if request.method == "OPTIONS":
        return _cors_json({})
    try:
        cid = int(request.query.get("cid", "0"))
        uid = int(request.query.get("uid", "0"))
    except Exception:
        return _cors_json({"error": "bad_params"}, 400)
    if not await _is_admin(app, cid, uid):
        return _cors_json({"error": "forbidden"}, 403)

    db = _db()
    days = int(request.query.get("days", "14"))
    d_end = date.today()
    d_start = d_end - timedelta(days=days - 1)

    top_rows = db["get_top_responders"](cid, d_start, d_end, 10) or []
    top = [{"user_id": u, "answers": n, "avg_ms": a} for (u, n, a) in top_rows]

    agg_raw = db["get_agg_rows"](cid, d_start, d_end) or []
    agg = [{"date": str(d), "messages": m, "active": au, "joins": j, "leaves": l, "kicks": k,
            "reply_p90_ms": p90, "spam_actions": spam}
           for (d, m, au, j, l, k, _p50, p90, _arh, _arhp, spam, _night) in agg_raw]

    return _cors_json({
        "daily_stats_enabled": db["is_daily_stats_enabled"](cid),
        "top_responders": top,
        "agg": agg,
    })

# === Bot-Befehle & WebAppData speichern ======================================
async def miniapp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # nur im Privatchat
    if update.effective_chat and update.effective_chat.type != "private":
        return
    if not update.effective_user or not update.effective_message:
        return

    user = update.effective_user
    msg = update.effective_message
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
        rows.append([InlineKeyboardButton(
            f"{title or cid} – Mini-App öffnen",
            web_app=WebAppInfo(url=_webapp_url(cid, title))
        )])

    if not rows:
        return await msg.reply_text("❌ Du bist in keiner registrierten Gruppe Admin/Owner.")

    await msg.reply_text("Wähle eine Gruppe:", reply_markup=InlineKeyboardMarkup(rows))

async def webapp_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not getattr(msg, "web_app_data", None):
        return

    # payload parsen
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

    # Welcome/Farewell/Rules ---------------------------------------------------
    try:
        if data.get("welcome_on"):
            text = (data.get("welcome_text") or "Willkommen {user} 👋").strip()
            db["set_welcome"](cid, None, text)  # (chat_id, photo_id, text)
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
            db["set_rules"](cid, None, data["rules_text"].strip())  # (chat_id, photo_id, text)
        else:
            db["delete_rules"](cid)
    except Exception as e:
        errors.append(f"Regeln: {e}")

    # Clean Deleted (nur Flag, Details optional)
    try:
        if "clean_deleted" in data:
            # falls du eine DB-Funktion dafür hast – hier nur als Beispiel:
            # db["set_clean_deleted_settings"](cid, enabled=bool(data.get("clean_deleted")))
            pass
    except Exception as e:
        errors.append(f"Aufräumen: {e}")

    # Spam/Links global + Topic-Override --------------------------------------
    try:
        admins_only   = bool(data.get("admins_only"))
        warning_on    = bool(data.get("warning_on"))
        warning_text  = (data.get("warning_text") or "").strip() or None
        exceptions_on = bool(data.get("exceptions_on"))

        db["set_link_settings"](cid,
            admins_only=admins_only,     # Alias → protection in DB
            warning_on=warning_on,
            warning_text=warning_text,
            exceptions_on=exceptions_on,
        )  # schreibt group_settings inkl. Warnung/Ausnahmen

        topic_id_raw = (data.get("topic_id") or "").strip()
        topic_id = int(topic_id_raw) if topic_id_raw.isdigit() else None

        wl = [x.strip().lower().lstrip(".") for x in (data.get("whitelist") or "").splitlines() if x.strip()]
        bl = [x.strip().lower().lstrip(".") for x in (data.get("blacklist") or "").splitlines() if x.strip()]

        spam_action = (data.get("spam_action") or "").strip().lower()
        if spam_action not in ("delete", "mute"):
            spam_action = None

        per_day = data.get("topic_limit")
        try:
            per_day = int(per_day) if per_day is not None and str(per_day).strip() != "" else None
        except Exception:
            per_day = None

        quota_notify = (data.get("quota_notify") or "").strip().lower()
        if quota_notify not in ("off", "smart", "always"):
            quota_notify = None

        if topic_id is not None:
            fields = {}
            if wl:            fields["link_whitelist"] = wl
            if bl:            fields["domain_blacklist"] = bl
            if spam_action:   fields["action_primary"] = spam_action
            if per_day is not None: fields["per_user_daily_limit"] = per_day
            if quota_notify:  fields["quota_notify"] = quota_notify
            if fields:
                db["set_spam_policy_topic"](cid, topic_id, **fields)
    except Exception as e:
        errors.append(f"Spam/Links: {e}")

    # RSS ----------------------------------------------------------------------
    try:
        post_images = bool(data.get("rss_images"))
        add_list = [u.strip() for u in (data.get("rss_add") or "").splitlines() if u.strip()]
        del_list = [u.strip() for u in (data.get("rss_del") or "").splitlines() if u.strip()]

        # auf das (evtl.) vorhandene Topic mappen
        try:
            rss_topic = db["get_rss_topic"](cid) or 0
        except Exception:
            rss_topic = 0

        for url in add_list:
            try:
                db["add_rss_feed"](cid, url, rss_topic)
                db["set_rss_feed_options"](cid, url, post_images=post_images)
            except Exception as e:
                errors.append(f"RSS add {url}: {e}")
        for url in del_list:
            try:
                db["remove_rss_feed"](cid, url)
            except Exception as e:
                errors.append(f"RSS del {url}: {e}")
    except Exception as e:
        errors.append(f"RSS: {e}")

    # AI / FAQ -----------------------------------------------------------------
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

    # Daily Stats --------------------------------------------------------------
    try:
        if "daily_stats" in data:
            db["set_daily_stats"](cid, bool(data.get("daily_stats")))
    except Exception as e:
        errors.append(f"Report: {e}")

    # Mood ---------------------------------------------------------------------
    try:
        if (data.get("mood_question") or "").strip():
            db["set_mood_question"](cid, data["mood_question"].strip())
        if (data.get("mood_topic") or "").strip().isdigit():
            db["set_mood_topic"](cid, int(data["mood_topic"].strip()))
    except Exception as e:
        errors.append(f"Mood: {e}")

    # Sprache ------------------------------------------------------------------
    try:
        if (data.get("language") or "").strip():
            db["set_group_language"](cid, data["language"].strip()[:5])
    except Exception as e:
        errors.append(f"Sprache: {e}")

    # Nachtmodus ---------------------------------------------------------------
    try:
        night = data.get("night") or {}
        if "on" in night or "start" in night or "end" in night:
            enabled = bool(night.get("on"))
            start_m = _hm_to_min(night.get("start") or "22:00", 1320)
            end_m   = _hm_to_min(night.get("end") or "07:00", 360)
            db["set_night_mode"](cid, enabled=enabled, start_minute=start_m, end_minute=end_m)
    except Exception as e:
        errors.append(f"Nachtmodus: {e}")

    # Router -------------------------------------------------------------------
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

    # Pro kaufen -------------------------------------------------------------------
    try:
        months = int(data.get("pro_months") or 0)
        if months > 0:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            until = datetime.now(ZoneInfo("UTC")) + timedelta(days=30*months)
            db["set_pro_until"](cid, until, tier="pro")
    except Exception as e:
        errors.append(f"Pro-Abo: {e}")
    
    if errors:
        return await msg.reply_text("⚠️ Teilweise gespeichert:\n• " + "\n• ".join(errors))
    return await msg.reply_text("✅ Einstellungen gespeichert.")

# === Registrierung ============================================================
def register_miniapp(app: Application):
    # 1) /miniapp nur im Privatchat
    app.add_handler(CommandHandler("miniapp", miniapp_cmd, filters=filters.ChatType.PRIVATE))

    # 2) WebAppData: wir filtern nur auf Privat – Handler selbst prüft web_app_data
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, webapp_data_handler))

    # 3) AIOHTTP-Routen für State & Stats (nur wenn Webhook/AIOHTTP aktiv)
    webapp = None
    try:
        # PTB >= 20: webhook_application() liefert aiohttp.web.Application
        webapp = app.webhook_application()
    except Exception:
        # Fallbacks ignorieren – dann gibt’s eben keine HTTP-Routen
        webapp = None

    if webapp:
        webapp["ptb_app"] = app
        webapp.router.add_route("GET", "/miniapp/state", route_state)
        webapp.router.add_route("OPTIONS", "/miniapp/state", route_state)
        webapp.router.add_route("GET", "/miniapp/stats", route_stats)
        webapp.router.add_route("OPTIONS", "/miniapp/stats", route_stats)
        # NEU:
        webapp.router.add_route("GET", "/miniapp/file", _file_proxy)
        webapp.router.add_route("OPTIONS", "/miniapp/file", _file_proxy)
        logger.info("[miniapp] HTTP-Routen registriert")
    else:
        logger.info("[miniapp] Keine AIOHTTP-App verfügbar – /miniapp/state|stats nicht aktiv.")
