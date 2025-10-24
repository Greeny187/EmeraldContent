import base64
import json
import os
import urllib.parse
import logging
import asyncio
import hmac, hashlib
from io import BytesIO
from aiohttp import web
from aiohttp.web_response import Response
from typing import List, Tuple, Optional
from zoneinfo import ZoneInfo
from datetime import date, timedelta, datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, InputFile
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# === Konfiguration ============================================================
MINIAPP_URL = os.getenv(
    "MINIAPP_URL",
    # dein neuer Pfad:
    "https://greeny187.github.io/EmeraldContentBots/miniapp/appcontent.html"
)
MINIAPP_API_BASE = os.getenv("MINIAPP_API_BASE", "").rstrip("/")
BOT_TOKEN = (os.getenv("BOT1_TOKEN"))
SECRET = hashlib.sha256(BOT_TOKEN.encode()).digest() if BOT_TOKEN else None

TOKENS = [
    os.getenv("BOT1_TOKEN"),
    os.getenv("BOT2_TOKEN"),
    os.getenv("BOT3_TOKEN"),
    os.getenv("BOT4_TOKEN"),
    os.getenv("BOT5_TOKEN"),
    os.getenv("BOT6_TOKEN"),
]
TOKENS = [t for t in TOKENS if t]  # nur gesetzte Tokens

# Sammelbecken für alle Tokens (auch dynamisch von PTB-Apps)
_ALL_TOKENS: set[str] = set(TOKENS)

def _all_token_secrets() -> list[bytes]:
    secs: list[bytes] = []
    for t in list(_ALL_TOKENS):
        try:
            secs.append(hashlib.sha256(t.encode()).digest())
        except Exception:
            pass
    return secs

def _verify_with_secret(init_data: str, secret: bytes) -> int:
    # Nach Telegram-Doku: data_check_string = sortierte key=value-Liste ohne 'hash'
    parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    recv_hash = parsed.get("hash")
    if not recv_hash:
        return 0
    items = [(k, v) for k, v in parsed.items() if k != "hash"]
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(items))
    calc = hmac.new(secret, msg=check_str.encode(), digestmod=hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, recv_hash):
        return 0
    try:
        user = json.loads(parsed.get("user") or "{}")
        return int(user.get("id") or 0)
    except Exception:
        return 0

def _verify_init_data_any(init_data: str) -> int:
    if not init_data:
        return 0
    # 1) hash-Variante gegen alle bekannten Tokens
    for secret in _all_token_secrets():
        uid = _verify_with_secret(init_data, secret)
        if uid > 0:
            return uid
    # 2) Fallback: Einige Clients liefern 'signature'; wir nutzen dann
    #    *nur* die user.id ohne kryptografische Prüfung – Admin-Check folgt serverseitig.
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        if parsed.get("signature") and parsed.get("user"):
            user = json.loads(parsed["user"])
            return int(user.get("id") or 0)
    except Exception:
        pass
    return 0

def _resolve_uid(request: web.Request) -> int:
    # 1) Telegram WebApp Header zuerst
    init_str = (request.headers.get("X-Telegram-Init-Data")
            or request.query.get("init_data")
            or request.headers.get("x-telegram-web-app-data"))  # optionaler Fallback
    uid = _verify_init_data_any(init_str) if init_str else 0

    if uid > 0:
        return uid
    # 2) Fallback: Query (für frühen Browser-Test)
    q_uid = request.query.get("uid")
    if q_uid and str(q_uid).lstrip("-").isdigit():
        return int(q_uid)
    # 3) Optionaler Dev-Bypass
    if os.getenv("ALLOW_BROWSER_DEV") == "1" and request.headers.get("X-Dev-Token") == os.getenv("DEV_TOKEN", ""):
        return int(request.headers.get("X-Dev-User-Id", "0") or 0)
    return 0

def _clean_dict_empty_to_none(d: dict) -> dict:
    """Konvertiert leere Strings in einem dict zu None."""
    return {k: (None if (isinstance(v, str) and v.strip() == "") else v) for k, v in d.items()}

def _topic_id_or_none(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("", "0", "none", "null"):
        return None
    try:
        return int(s)
    except Exception:
        return None

def _none_if_blank(v):
    return None if (v is None or (isinstance(v, str) and v.strip() == "")) else v

async def _upload_get_file_id(app: Application, target_chat_id: int, base64_str: str) -> str | None:
    """
    Nimmt eine Data-URL/Base64 und lädt das Bild via Bot hoch. Gibt file_id zurück.
    Wichtig: Upload NICHT mehr in die Gruppe, sondern per DM an den Admin (target_chat_id=uid),
    damit nichts in der Gruppe gepostet wird.
    """
    if not base64_str:
        return None
    b64 = base64_str.split(",", 1)[-1]
    raw = base64.b64decode(b64)
    bio = BytesIO(raw); bio.name = "upload.jpg"
    try:
        # bevorzugt DM an den Admin (leise)
        msg = await app.bot.send_photo(target_chat_id, InputFile(bio), disable_notification=True)
    except Exception as e:
        # Fallback: versuche nochmal den gleichen Chat (kein Gruppen-Spam!)
        logger.warning(f"[miniapp] DM-Upload fehlgeschlagen: {e}")
        return None
    return msg.photo[-1].file_id if msg.photo else None

# ---------- Gemeinsame Speicherroutine (von beiden Wegen nutzbar) ----------
async def _save_from_payload(cid:int, uid:int, data:dict, app:Application|None) -> list[str]:
    db = _db()
    errors: list[str] = []
 
    # --- Vorverarbeitung: Kompatibilität für { images: { welcome|rules|farewell } } -----------
    try:
        imgs = data.get("images") or {}
        for k in ("welcome","rules","farewell"):
            val = imgs.get(k)
            if isinstance(val, dict):
                d = data.setdefault(k, {})
                if val.get("clear") is True:
                    d["img_base64"] = ""   # explizit Foto löschen
                elif val.get("img_base64"):
                    d["img_base64"] = val["img_base64"]
    except Exception:
        pass

    # Hilfsfunktion: nie auto-löschen, nur bei expliziten Flags
    async def _upsert_media(kind:str, block:dict):
        # bestehende Werte laden
        try:
            getter = {"welcome":"get_welcome","rules":"get_rules","farewell":"get_farewell"}[kind]
            setter = {"welcome":"set_welcome","rules":"set_rules","farewell":"set_farewell"}[kind]
            deleter= {"welcome":"delete_welcome","rules":"delete_rules","farewell":"delete_farewell"}[kind]
        except KeyError:
            return
        try:
            existing_photo, existing_text = db[getter](cid) or (None, None)
        except Exception:
            existing_photo, existing_text = (None, None)

        on_present = ("on" in block)
        on_value   = bool(block.get("on")) if on_present else None

        # Text: nur übernehmen, wenn im Payload vorhanden, sonst beibehalten
        text = (block.get("text") if "text" in block else existing_text)
        if text is not None:
            text = _none_if_blank(text)

        # Bild: nur ändern, wenn img_base64 im Payload vorhanden
        photo_id = existing_photo
        if "img_base64" in block:
            v = block.get("img_base64")
            if isinstance(v, str) and v == "":
                photo_id = None                       # nur Foto löschen
            elif app and _none_if_blank(v):
                tmp = await _upload_get_file_id(app, uid, v)  # DM-Upload
                if tmp: photo_id = tmp

        # Löschen NUR wenn explizit on:false
        if on_present and on_value is False:
            db[deleter](cid)
            return

        # Speichern, falls Text oder Foto da ist – sonst NIX (kein Auto-Delete!)
        if (text or photo_id):
            db[setter](cid, photo_id, text)
        # Falls explizit on:true aber ohne Inhalt → trotzdem nichts löschen    
        
    # --- Captcha ---
    try:
        if "captcha" in data:
            c = data["captcha"] or {}
            # bestehende Werte holen, damit wir nie NOT NULL verletzen
            try:
                old_enabled, old_type, old_behavior = db["get_captcha_settings"](cid)
            except Exception:
                old_enabled, old_type, old_behavior = (False, "button", "kick")
            enabled  = bool(c.get("enabled")) if ("enabled" in c) else bool(old_enabled)
            ctype    = (c.get("type") or old_type or "button")
            if ctype not in ("button", "math"): ctype = old_type or "button"
            behavior = (c.get("behavior") or old_behavior or "kick")
            if behavior not in ("kick", "mute", "none"): behavior = old_behavior or "kick"
            db["set_captcha_settings"](cid, enabled, ctype, behavior)
    except Exception as e:
        errors.append(f"Captcha: {e}")

    # --- Welcome ---
    try:
        if "welcome" in data:
            await _upsert_media("welcome", data.get("welcome") or {})
    except Exception as e:
        errors.append(f"Welcome: {e}")

    # --- Rules ---
    try:
        if "rules" in data:
            await _upsert_media("rules", data.get("rules") or {})
    except Exception as e:
        errors.append(f"Rules: {e}")

    # --- Farewell ---
    try:
        if "farewell" in data:
            await _upsert_media("farewell", data.get("farewell") or {})
    except Exception as e:
        errors.append(f"Farewell: {e}")

    # --- Links/Spam ---
    try:
        sp = data.get("spam") or {}
        admins_only = bool(sp.get("on") or sp.get("block_links") or data.get("admins_only"))
        db["set_link_settings"](cid, admins_only=admins_only)

        t_raw = str(sp.get("policy_topic") or "").strip()
        topic_id = int(t_raw) if t_raw.isdigit() else None

        def _to_list(v):
            if isinstance(v, list): return [str(x).strip() for x in v if str(x).strip()]
            if isinstance(v, str):  return [s.strip() for line in v.splitlines() for s in line.split(",") if s.strip()]
            return []

        fields = {}
        wl=_to_list(sp.get("whitelist","")); bl=_to_list(sp.get("blacklist",""))
        if wl: fields["link_whitelist"]   = wl
        if bl: fields["domain_blacklist"] = bl
        act=(sp.get("action") or "").strip().lower()
        if act in ("delete","warn","mute"): fields["action_primary"] = act
        lim=str(sp.get("per_user_daily_limit") or "").strip()
        if lim.isdigit(): fields["per_user_daily_limit"] = int(lim)
        qn=(sp.get("quota_notify") or "").strip().lower()
        if qn in ("off","smart","always"): fields["quota_notify"]=qn

        if topic_id is not None and fields:
            db["set_spam_policy_topic"](cid, topic_id, **fields)
    except Exception as e:
        errors.append(f"Spam/Links: {e}")

    # --- RSS add/del/update ---
    if "rss" in data or "rss_update" in data or "rss_del" in data:
        r = data.get("rss") or {}
        if (r.get("url") or "").strip():
            url = r.get("url").strip()
            topic = int(r.get("topic") or 0)
            try: db["set_rss_topic"](cid, topic)
            except Exception: pass
            db["add_rss_feed"](cid, url, topic)
            db["set_rss_feed_options"](cid, url, post_images=bool(r.get("post_images")), enabled=bool(r.get("enabled", True)))
            logger.info(f"[miniapp] RSS add cid={cid} url={url} topic={topic} post_images={bool(r.get('post_images'))} enabled={bool(r.get('enabled', True))}")
        upd = data.get("rss_update") or None
        if upd and (upd.get("url") or "").strip():
            url=upd.get("url").strip()
            db["set_rss_feed_options"](cid, url, post_images=upd.get("post_images"), enabled=upd.get("enabled"))
        if data.get("rss_del"):
            del_url = data.get("rss_del")
            db["remove_rss_feed"](cid, del_url)
            logger.info(f"[miniapp] RSS del cid={cid} url={del_url}")
        if "rss_update" in data:
            u = data["rss_update"]; logger.info(f"[miniapp] RSS update cid={cid} {u}")

    # --- KI (Assistent/FAQ) ---
    try:
        ai = data.get("ai") or {}
        faq_on = bool(ai.get("on") or (ai.get("faq") or "").strip())
        db["set_ai_settings"](cid, faq=faq_on, rss=None)
    except Exception as e:
        errors.append(f"KI: {e}")

    # --- FAQ add/del ---
    try:
        faq_add = data.get("faq_add") or None
        if faq_add and (faq_add.get("q") or "").strip():
            db["upsert_faq"](cid, faq_add["q"].strip(), (faq_add.get("a") or "").strip())
        faq_del = data.get("faq_del") or None
        if faq_del and (faq_del.get("q") or "").strip():
            db["delete_faq"](cid, faq_del["q"].strip())
    except Exception as e:
        errors.append(f"FAQ: {e}")

    # --- KI-Moderation ---
    try:
        aimod = data.get("ai_mod") or {}
        if aimod:
            # Leere Strings zu None konvertieren!
            aimod_clean = _clean_dict_empty_to_none(aimod)
            allowed = {
              "enabled","shadow_mode","action_primary","mute_minutes","warn_text","appeal_url",
              "max_per_min","cooldown_s","exempt_admins","exempt_topic_owner",
              "toxicity","hate","sexual","harassment","selfharm","violence",
              "tox_thresh","hate_thresh","sex_thresh","harass_thresh","selfharm_thresh","violence_thresh"
            }
            payload={}
            for k in allowed:
                if k in aimod_clean and aimod_clean[k] is not None:
                    payload[k]=aimod_clean[k]
            alias={"toxicity":"tox_thresh","hate":"hate_thresh","sexual":"sex_thresh",
                   "harassment":"harass_thresh","selfharm":"selfharm_thresh","violence":"violence_thresh"}
            for k,v in list(payload.items()):
                if k in alias: payload[alias[k]]=v; del payload[k]
            db["set_ai_mod_settings"](cid, 0, **payload)
            logger.info("[miniapp] AIMOD: %s", payload)
    except Exception as e:
        errors.append(f"AI-Mod: {e}")

    # --- Daily Report ---
    try:
        if "daily_stats" in data:
            db["set_daily_stats"](cid, bool(data.get("daily_stats")))
    except Exception as e:
        errors.append(f"Daily-Report: {e}")
    try:
        if data.get("report_send_now") and app:
            cfg = data.get("report", {}) or {}
            dest = (cfg.get("dest") or "dm").lower()
            topic_id = int(cfg.get("topic") or 0) or None

            d1 = date.today()
            d0 = d1  # „heute“; optional auf 7d erweitern
            summary = db["get_agg_summary"](cid, d0, d1)
            top = db["get_top_responders"](cid, d0, d1, 5) or []

            def _fmt_ms(ms):
                if ms is None: return "–"
                s = int(ms//1000)
                return f"{s//60}m {s%60}s" if s>=60 else f"{s}s"

            lines = [
            "📊 <b>Statistik</b> (heute)",
            f"• Nachrichten: <b>{summary['messages_total']}</b>",
            f"• Aktive Nutzer: <b>{summary['active_users']}</b>",
            f"• Joins/Leaves/Kicks: <b>{summary['joins']}/{summary['leaves']}/{summary['kicks']}</b>",
            f"• Antwortzeiten p50/p90: <b>{_fmt_ms(summary['reply_median_ms'])}/{_fmt_ms(summary['reply_p90_ms'])}</b>",
            f"• Assist-Hits/Helpful: <b>{summary['autoresp_hits']}/{summary['autoresp_helpful']}</b>",
            f"• Moderation (Spam/Nacht): <b>{summary['spam_actions']}/{summary['night_deletes']}</b>",
            ]
            if top:
                lines.append("<b>Top-Responder</b>")
                for (u, answers, avg_ms) in top:
                    s = int((avg_ms or 0)//1000)
                    s_str = f"{s//60}m {s%60}s" if s>=60 else f"{s}s"
                    lines.append(f"• <code>{u}</code>: <b>{answers}</b> Antworten, Ø {s_str}")
            text = "\n".join(lines)

            target_chat = cid if dest=="topic" else uid
            kw = {}
            if dest=="topic" and topic_id: kw["message_thread_id"] = topic_id

            await app.bot.send_message(chat_id=target_chat, text=text, parse_mode="HTML", **kw)
            logger.info(f"[miniapp] Statistik gesendet: dest={dest} chat={target_chat} topic={topic_id}")
    except Exception as e:
        logger.error(f"[miniapp] Fehler beim Senden der Statistik: {e}")
    # --- Mood ---
    try:
        if data.get("mood_send_now") and app:
            question = db["get_mood_question"](cid) or "Wie ist deine Stimmung?"
            topic_id = db["get_mood_topic"](cid) or None
            kb = InlineKeyboardMarkup([[  # wie gehabt …
                InlineKeyboardButton("👍", callback_data="mood_like"),
                InlineKeyboardButton("👎", callback_data="mood_dislike"),
                InlineKeyboardButton("🤔", callback_data="mood_think"),
            ]])
            await app.bot.send_message(chat_id=cid, text=question,
                                    message_thread_id=topic_id if topic_id else None,
                                    reply_markup=kb)
            logger.info(f"[miniapp] Mood prompt gesendet in {cid} (topic={topic_id})")
    except Exception as e:
        errors.append(f"Mood: {e}")

    # --- Sprache ---
    try:
        lang=(data.get("language") or "").strip()
        if lang: db["set_group_language"](cid, lang[:5])
    except Exception as e:
        errors.append(f"Sprache: {e}")

    # --- Clean Deleted: Scheduler speichern & Sofort-Aktion ---
    try:
        cd = data.get("clean_deleted") or None
        if cd is not None:
            hh, mm = 3, 0
            try:
                hh, mm = map(int, (cd.get("time") or "03:00").split(":"))
            except Exception:
                pass
            wd = cd.get("weekday", None)
            if wd == "" or wd is False:
                wd = None
            db["set_clean_deleted_settings"](cid,
                enabled = bool(cd.get("enabled")),
                hh = int(hh), mm = int(mm),
                weekday = wd if wd is None else int(wd),
                demote = bool(cd.get("demote")),
                notify = bool(cd.get("notify")),
            )
        if data.get("clean_delete_now"):
            from .utils import clean_delete_accounts_for_chat
            asyncio.create_task(clean_delete_accounts_for_chat(cid, app.bot))
    except Exception as e:
        errors.append(f"CleanDelete: {e}")
    
    # --- Nachtmodus ---
    try:
        night = data.get("night") or {}
        if ("on" in night) or ("start" in night) or ("end" in night) or ("timezone" in night) or ("override_until" in night):
            def _hm_to_min(s, default):
                try:
                    h, m = str(s or '').split(':'); return int(h)*60 + int(m)
                except Exception:
                    return default

            enabled = bool(night.get("on"))
            start_m = _hm_to_min(night.get("start") or "22:00", 1320)
            end_m   = _hm_to_min(night.get("end") or "07:00", 360)

            tz = (night.get("timezone") or "Europe/Berlin").strip() or "Europe/Berlin"
            override_until = night.get("override_until")
            if isinstance(override_until, str):
                s = override_until.strip()
                if not s:
                    override_until = None
                else:
                    # datetime-local ohne TZ → als lokale Zeit interpretieren und nach UTC konvertieren
                    # Erwartete Formate: "YYYY-MM-DDTHH:MM" oder ISO mit Sekunden
                    try:
                        dt_local = datetime.fromisoformat(s)
                        if dt_local.tzinfo is None:
                            dt_local = dt_local.replace(tzinfo=ZoneInfo(tz))
                        override_until = dt_local.astimezone(ZoneInfo("UTC"))
                    except Exception:
                        # Fallback: lieber None als kaputter String
                        override_until = None

            db["set_night_mode"](cid,
                enabled=enabled,
                start_minute=start_m,
                end_minute=end_m,
                delete_non_admin_msgs=night.get("delete_non_admin_msgs"),
                warn_once=night.get("warn_once"),
                timezone=tz,
                hard_mode=night.get("hard_mode"),
                override_until=override_until
            )
    except Exception as e:
        errors.append(f"Nachtmodus: {e}")

    # --- Topic Router ---
    try:
        if "router_add" in data:
            r = data["router_add"]
            db["add_topic_router_rule"](cid, r["pattern"], r["target_topic_id"])
        if "router_toggle" in data:
            r = data["router_toggle"]
            db["toggle_topic_router_rule"](cid, r["rule_id"], r.get("enabled", True))
        if "router_delete" in data:
            r = data["router_delete"]
            db["delete_topic_router_rule"](cid, r["rule_id"])
    except Exception as e:
        errors.append(f"TopicRouter: {e}")

    # --- Pro kaufen/verlängern ---
    try:
        months = int(data.get("pro_months") or 0)
        if months>0:
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo
            until = datetime.now(ZoneInfo("UTC")) + timedelta(days=30*months)
            db["set_pro_until"](cid, until, tier="pro")
    except Exception as e:
        errors.append(f"Pro-Abo: {e}")

        # --- Clean Deleted Accounts (Einmal-Aktion) ---
    try:
        if data.get("clean_delete_now"):
            from .utils import clean_delete_accounts_for_chat
            # nicht blockieren
            asyncio.create_task(clean_delete_accounts_for_chat(cid, app.bot))
    except Exception as e:
        errors.append(f"CleanDelete: {e}")

    return errors




# ---------- HTTP-Fallback: /miniapp/apply ----------
async def route_apply(request):
    app: Application = request.app["ptb_app"]
    if request.method == "OPTIONS":
        return _cors_json({})
    # Parse JSON payload once (avoid consuming the stream twice)
    try:
        data = await request.json()
    except Exception:
        data = {}
    logger.info("[miniapp] APPLY cid=%s uid=%s keys=%s",
                request.query.get("cid"),
                _resolve_uid(request),
                list(data.keys()))
    cid = int(request.query.get("cid", "0") or 0)
    uid = _resolve_uid(request)
    if uid <= 0:
        return _cors_json({"error": "auth_required"}, 403)
    
    if not await _is_admin(app, cid, uid):
        return _cors_json({"error": "forbidden"}, 403)

    if not cid:
        return web.Response(status=400, text="cid fehlt")

    # Optional: Hier könnte man get_chat_member aufrufen, um Adminrechte zu prüfen
    # Für die Mini-App-Entwicklung erlauben wir den HTTP-Save.

    errors = await _save_from_payload(cid, uid, data, request.app["ptb_app"])
    if errors:
        return web.Response(status=207, text="Teilweise gespeichert:\n- " + "\n- ".join(errors))
    return web.Response(text="✅ Einstellungen gespeichert.")

async def route_file(request: web.Request):
    """Proxy für Telegram-Bilder per file_id – robust & CORS-freundlich."""
    webapp = request.app
    try:
        file_id = request.query.get("id")
        if not file_id:
            raise ValueError("missing file_id")
        app = webapp["ptb_app"]
        f = await app.bot.get_file(file_id)
        blob = await f.download_as_bytearray()
        ctype = "image/jpeg"
        return web.Response(
            body=blob, content_type=ctype,
            headers={
                "Cache-Control":"public, max-age=86400",
                "Access-Control-Allow-Origin": webapp["allowed_origin"]
            }
        )
    except Exception as e:
        logger.warning(f"[miniapp] file proxy failed: {e}")
        return web.Response(status=404, text="not found",
                            headers={"Access-Control-Allow-Origin": request.app.get("allowed_origin","*")})


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
    # Nur noch lokale DB – kein shared.database mehr
    try:
        from .database import (
            get_registered_groups,get_clean_deleted_settings, set_clean_deleted_settings,
            set_welcome, delete_welcome, get_welcome,
            set_rules, delete_rules, get_rules, get_group_stats,
            set_farewell, delete_farewell, get_farewell, get_agg_summary, get_heatmap,
            get_link_settings, set_link_settings, set_spam_policy_topic,
            set_rss_topic, get_rss_topic, add_rss_feed, remove_rss_feed, set_rss_feed_options, list_rss_feeds,
            get_ai_settings, set_ai_settings, upsert_faq, delete_faq,
            set_daily_stats, is_daily_stats_enabled, get_top_responders, get_agg_rows,
            set_mood_question, get_mood_question, set_mood_topic, get_mood_topic,
            set_group_language, set_night_mode, add_topic_router_rule, get_effective_link_policy, 
            get_rss_feeds_full, get_subscription_info, effective_ai_mod_policy, get_ai_mod_settings, 
            set_ai_mod_settings, list_faqs, list_topic_router_rules, get_night_mode, set_pro_until,
            get_captcha_settings, set_captcha_settings
        )
        # explizit ein dict bauen, damit die Funktionen korrekt referenziert werden
        return {
            "get_clean_deleted_settings": get_clean_deleted_settings,
            "set_clean_deleted_settings": set_clean_deleted_settings,
            "get_captcha_settings": get_captcha_settings,
            "set_captcha_settings": set_captcha_settings,
            "get_agg_summary": get_agg_summary,
            "get_heatmap": get_heatmap,
            "get_registered_groups": get_registered_groups,
            "set_welcome": set_welcome,
            "delete_welcome": delete_welcome,
            "get_welcome": get_welcome,
            "set_rules": set_rules,
            "delete_rules": delete_rules,
            "get_rules": get_rules,
            "set_farewell": set_farewell,
            "delete_farewell": delete_farewell,
            "get_farewell": get_farewell,
            "get_link_settings": get_link_settings,
            "set_link_settings": set_link_settings,
            "set_spam_policy_topic": set_spam_policy_topic,
            "set_rss_topic": set_rss_topic,
            "get_rss_topic": get_rss_topic,
            "add_rss_feed": add_rss_feed,
            "remove_rss_feed": remove_rss_feed,
            "set_rss_feed_options": set_rss_feed_options,
            "list_rss_feeds": list_rss_feeds,
            "get_ai_settings": get_ai_settings,
            "set_ai_settings": set_ai_settings,
            "upsert_faq": upsert_faq,
            "delete_faq": delete_faq,
            "set_daily_stats": set_daily_stats,
            "is_daily_stats_enabled": is_daily_stats_enabled,
            "get_group_stats": get_group_stats,
            "get_top_responders": get_top_responders,
            "get_agg_rows": get_agg_rows,
            "set_mood_question": set_mood_question,
            "get_mood_question": get_mood_question,
            "set_mood_topic": set_mood_topic,
            "get_mood_topic": get_mood_topic,
            "set_group_language": set_group_language,
            "set_night_mode": set_night_mode,
            "add_topic_router_rule": add_topic_router_rule,
            "get_effective_link_policy": get_effective_link_policy,
            "get_rss_feeds_full": get_rss_feeds_full,
            "get_subscription_info": get_subscription_info,
            "effective_ai_mod_policy": effective_ai_mod_policy,
            "get_ai_mod_settings": get_ai_mod_settings,
            "set_ai_mod_settings": set_ai_mod_settings,
            "list_faqs": list_faqs,
            "list_topic_router_rules": list_topic_router_rules,
            "get_night_mode": get_night_mode,
            "set_pro_until": set_pro_until,
            # ggf. weitere Funktionen ergänzen
        }
    except ImportError as e:
        logger.error(f"Database import failed: {e}")
        # Dummy-Funktionen als Fallback
        def dummy(*args, **kwargs):
            return None
        return {name: dummy for name in [
            'get_registered_groups', 'set_welcome', 'delete_welcome', 'get_welcome']
            # ... alle anderen benötigten Funktionen ...
        }
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

async def _is_admin(app_or_webapp, cid: int, uid: int) -> bool:
    """Prüft Adminrechte über *alle* bekannten PTB-Apps."""
    apps: list[Application] = []
    try:
        # Falls eine einzelne App übergeben wurde
        if isinstance(app_or_webapp, Application):
            apps = [app_or_webapp]
        else:
            # AIOHTTP WebApp → alle gesammelten Apps
            apps = list(app_or_webapp.get("_ptb_apps", []))
            # Fallback: alte Einzel-Referenz
            if not apps and "ptb_app" in app_or_webapp:
                apps = [app_or_webapp["ptb_app"]]
    except Exception:
        apps = []
    for a in apps:
        try:
            member = await a.bot.get_chat_member(cid, uid)
            if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                return True
        except Exception:
            continue
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
            "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data, X-Dev-Token, X-Dev-User-Id",
        }
    )

async def _file_proxy(request):
    app = request.app["ptb_app"]
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
        # irgendeine App darf die Datei holen – wir nehmen die erste verfügbare
        apps = request.app.get("_ptb_apps", [])
        if not apps:
            return _cors_json({"error":"no_bot"}, 500)
        f = await apps[0].bot.get_file(fid)
        buf = BytesIO()
        await f.download_to_memory(out=buf)
        buf.seek(0)
        return Response(body=buf.read(), headers={"Access-Control-Allow-Origin": ALLOWED_ORIGIN},
                        content_type="application/octet-stream")
    except Exception:
        return _cors_json({"error":"not_found"}, 404)

# --- kleine Helferblöcke (DB-Aufrufe sauber gekapselt) -----------------------
def _mk_media_block(cid:int, kind:str):
    loader = {"welcome": "get_welcome", "rules": "get_rules", "farewell":"get_farewell"}[kind]
    ph, tx = (None, None)
    try:
        r = _db()[loader](cid)
        if r: ph, tx = r
    except Exception:
        pass
    return {"on": bool(tx), "text": tx or "", "photo": bool(ph), "photo_id": ph or ""}

async def _state_json(cid: int) -> dict:
    db = _db()
    # Links/Spam
    try:
        eff = db["get_effective_link_policy"](cid, None) or {}
    except Exception:
        eff = {}
    try:
        link = db["get_link_settings"](cid) or {}
    except Exception:
        link = {}

    # RSS voll
    feeds = []
    try:
        for (c,url,topic,etag,lm,post_images,enabled) in db["get_rss_feeds_full"]():
            if c == cid:
                feeds.append({"url":url, "topic":topic, "post_images":bool(post_images), "enabled":bool(enabled)})
    except Exception:
        pass

    # Subscription
    try:
        sub = db["get_subscription_info"](cid) or {}
    except Exception:
        sub = {"tier":"free","active":False,"valid_until":None}

    # AI-Moderation
    try:
        aimod_eff = db["effective_ai_mod_policy"](cid) or {}
        aimod_cfg = db["get_ai_mod_settings"](cid, 0) or {}
        aimod = {**aimod_eff, **aimod_cfg}
    except Exception:
        aimod = {}

    # FAQs
    try:
        faqs = [{"q": q, "a": a} for (q, a) in (db["list_faqs"](cid) or [])]
    except Exception:
        faqs = []

    # Router
    try:
        rows = db["list_topic_router_rules"](cid) or []
        router_rules = [{
          "rule_id": r[0], "target_topic_id": r[1], "enabled": bool(r[2]),
          "delete_original": bool(r[3]), "warn_user": bool(r[4]),
          "keywords": r[5] or [], "domains": r[6] or []
        } for r in rows]
    except Exception:
        router_rules = []

    # Night mode
    try:
        (enabled, start_m, end_m, del_non_admin, warn_once, tz, hard, override_until) = db["get_night_mode"](cid)
        night = {
          "enabled": bool(enabled),
          "start": f"{start_m//60:02d}:{start_m%60:02d}",
          "end":   f"{end_m//60:02d}:{end_m%60:02d}",
          "delete_non_admin_msgs": bool(del_non_admin),
          "warn_once": bool(warn_once),
          "timezone": tz,
          "hard_mode": bool(hard),
          "override_until": override_until.isoformat() if override_until else None,
        }
    except Exception:
        night = {"enabled": False, "start": "22:00", "end":"07:00"}

    spam_block = {
        "on":           bool(eff.get("admins_only") or link.get("only_admin_links")),
        "block_links":  bool(eff.get("admins_only") or link.get("only_admin_links")),
        "block_media":  False,
        "block_invite_links": False,
        "policy_topic": 0,
        "whitelist":    eff.get("whitelist") or [],
        "blacklist":    eff.get("blacklist") or [],
        "action":       eff.get("action") or "delete",
        "per_user_daily_limit": 0,
        "quota_notify": None
    }

    # AI Flags
    try:
        (ai_faq, ai_rss) = db["get_ai_settings"](cid)
    except Exception:
        ai_faq, ai_rss = (False, False)

    # Welcome/Rules/Farewell mit Bild-URL
    def _media_block_with_image(cid, kind):
        """Bildfelder defensiv aufbauen – nie Exceptions werfen."""
        try:
            if kind=="welcome":
                ph, tx = _db()["get_welcome"](cid) or (None, None)
            elif kind=="rules":
                ph, tx = _db()["get_rules"](cid) or (None, None)
            else:
                ph, tx = _db()["get_farewell"](cid) or (None, None)
        except Exception as e:
            logger.warning(f"[miniapp] _media_block DB failed kind={kind}: {e}")
            ph, tx = (None, None)
        try:
            image_url = (f"/miniapp/file?id={ph}" if ph else None)
        except Exception:
            image_url = None
        return {
            "on": bool(tx) or bool(ph),          # aktiv, wenn Text ODER Bild vorhanden
            "text": tx or "",
            "photo": bool(ph),
            "photo_id": ph or "",
            "image_url": image_url
        }

    # Captcha-Block aus DB lesen
    try:
        en, ctype, behavior = db["get_captcha_settings"](cid)
        captcha = {"enabled": bool(en), "type": ctype, "behavior": behavior}
    except Exception:
        captcha = {"enabled": False, "type": "button", "behavior": "kick"}

    # Fix: call the function from db dict, not a string
    stats = db["get_group_stats"](cid, date.today()) if "get_group_stats" in db else {}

    # Clean-Deleted aus DB lesen
    try:
        cds = db["get_clean_deleted_settings"](cid)
    except Exception:
        cds = {"enabled": False, "hh":3, "mm":0, "weekday": None, "demote": False, "notify": True}
    clean_deleted = {
        "enabled": bool(cds.get("enabled")),
        "time": f"{int(cds.get('hh',3)):02d}:{int(cds.get('mm',0)):02d}",
        "weekday": cds.get("weekday"),
        "demote": bool(cds.get("demote")),
        "notify": bool(cds.get("notify")),
    }
    return {
      "welcome": _media_block_with_image(cid, "welcome"),
      "rules":   _media_block_with_image(cid, "rules"),
      "farewell":_media_block_with_image(cid, "farewell"),
      "captcha": captcha,
      "links":   {"only_admin_links": bool(link.get("only_admin_links"))},
      "spam":    spam_block,
      "ai":      {"on": bool(ai_faq or ai_rss), "faq": ""},
      "aimod":   aimod,
      "faqs":    faqs,
      "router_rules": router_rules,
      "mood":    {"topic": (db["get_mood_topic"](cid) or 0), "question": db["get_mood_question"](cid)},
      "rss":     {"feeds": feeds},
      "daily_stats": db["is_daily_stats_enabled"](cid),
      "subscription": sub,
      "night":   night,
      "language": db.get("get_group_language", lambda *_: None)(cid),
      "report": {"enabled": True, "stats": stats},
      "clean_deleted": clean_deleted,
    }


# === HTTP-Routen (nur lesend, Admin-Gate per Bot) ============================
async def route_state(request: web.Request):
    # Zugriff auf die AIOHTTP-App (enthält _ptb_apps)
    webapp = request.app
    if request.method == "OPTIONS":
        return _cors_json({})
    
    # Debug-Logging hinzufügen
    logger.info(f"Headers: {dict(request.headers)}")
    logger.info(f"Query params: {dict(request.query)}")
    
    try:
        cid = int(request.query.get("cid", "0") or 0)
        uid = _resolve_uid(request)
        logger.info(f"Resolved UID: {uid}, CID: {cid}")

        if uid <= 0:
            logger.warning("Authentication failed: UID <= 0")
            return _cors_json({"error": "auth_required"}, 403)

        if not await _is_admin(webapp, cid, uid):
            logger.warning(f"User {uid} is not admin in {cid}")
            return _cors_json({"error": "forbidden"}, 403)

    except Exception as e:
        # NIE 500 werfen wegen Bildproblemen – UI soll weiter funktionieren
        logger.exception(f"[miniapp] state failed for cid={cid}: {e}")
        # minimaler Fallback, damit UI nicht blockiert
        return _cors_json({"error":"state_failed", "welcome":{}, "rules":{}, "farewell":{}}, 200)

async def route_stats(request: web.Request):
    webapp = request.app
    if request.method == "OPTIONS":
        return _cors_json({})
    try:
        cid = int(request.query.get("cid", "0") or 0)
        uid = _resolve_uid(request)
        if uid <= 0:
            return _cors_json({"error": "auth_required"}, 403)
        if not await _is_admin(webapp, cid, uid):
            return _cors_json({"error": "forbidden"}, 403)

    except Exception:
        return _cors_json({"error": "bad_params"}, 400)
    if not await _is_admin(webapp, cid, uid):
        return _cors_json({"error": "forbidden"}, 403)

    db = _db()
    days = int(request.query.get("days", "14"))
    d_end = date.today()
    d_start = d_end - timedelta(days=days - 1)

    RATE = float(os.getenv("EMRLD_PER_ANSWER", "0.01"))
    top_rows = db["get_top_responders"](cid, d_start, d_end, 10) or []
    top = []
    for (u, n, a) in top_rows:
        reward = round((n or 0) * RATE, 4)
        top.append({"user_id": u, "answers": n, "avg_ms": a, "emrld": reward})

    agg_raw = db["get_agg_rows"](cid, d_start, d_end) or []
    agg = [{"date": str(d), "messages": m, "active": au, "joins": j, "leaves": l, "kicks": k,
            "reply_p90_ms": p90, "spam_actions": spam}
           for (d, m, au, j, l, k, _p50, p90, _arh, _arhp, spam, _night) in agg_raw]

    return _cors_json({
        "daily_stats_enabled": db["is_daily_stats_enabled"](cid),
        "top_responders": top,
        "agg": agg,
    })
    
async def route_send_mood(request: web.Request):
    webapp = request.app
    if request.method == "OPTIONS":
        return _cors_json({})
    try:
        cid = int(request.query.get("cid", "0") or 0)
        uid = _resolve_uid(request)
        if uid <= 0:
            return _cors_json({"error": "auth_required"}, 403)
        if not await _is_admin(webapp, cid, uid):
            return _cors_json({"error": "forbidden"}, 403)

    except Exception:
        return _cors_json({"error": "bad_params"}, 400)
    if not await _is_admin(webapp, cid, uid):
        return _cors_json({"error": "forbidden"}, 403)

    db = _db()
    question = db["get_mood_question"](cid) or "Wie ist deine Stimmung?"
    topic_id = db["get_mood_topic"](cid) or None

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👍", callback_data="mood_like"),
        InlineKeyboardButton("👎", callback_data="mood_dislike"),
        InlineKeyboardButton("🤔", callback_data="mood_think"),
    ]])

    try:
        apps = webapp.get("_ptb_apps", []) or [webapp["ptb_app"]]
        await apps[0].bot.send_message(chat_id=cid, text=question, reply_markup=kb,
                                       message_thread_id=topic_id)
        return _cors_json({"ok": True})
    except Exception as e:
        logger.error(f"[miniapp] send_mood failed: {e}")
        return _cors_json({"error":"send_failed"}, 500)

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

    logger.info("[miniapp] web_app_data received: uid=%s, len=%s",
                msg.from_user.id if msg.from_user else None,
                len(msg.web_app_data.data or ""))

    try:
        data = json.loads(update.effective_message.web_app_data.data)
    except Exception:
        return await msg.reply_text("❌ Ungültige Daten von der Mini-App.")

    # cid aus Payload ziehen (wie bei dir bisher)
    cid = None
    try:
        if "cid" in data: cid = int(data.get("cid"))
        elif "context" in data and "cid" in data["context"]: cid = int(data["context"]["cid"])
    except Exception:
        pass
    if not cid:
        return await msg.reply_text("❌ Gruppen-ID fehlt oder ist ungültig.")

    # Admincheck
    if not await _is_admin_or_owner(context, cid, update.effective_user.id):
        return await msg.reply_text("❌ Du bist in dieser Gruppe kein Admin.")

    errors = await _save_from_payload(cid, update.effective_user.id, data, context.application)

    if errors:
        return await msg.reply_text("⚠️ Teilweise gespeichert:\n• " + "\n• ".join(errors))
    
    db = _db()

    # Links/Spam
    try:
        sp = data.get("spam") or {}
        admins_only = bool(sp.get("on") or sp.get("block_links") or data.get("admins_only"))
        db["set_link_settings"](cid, admins_only=admins_only)

        t_raw = str(sp.get("policy_topic") or '').strip()
        topic_id = int(t_raw) if t_raw.isdigit() else None

        def _to_list(v):
            if isinstance(v, list): return [str(x).strip() for x in v if str(x).strip()]
            if isinstance(v, str):  return [s.strip() for line in v.splitlines() for s in line.split(',') if s.strip()]
            return []

        fields = {}
        wl=_to_list(sp.get("whitelist","")); bl=_to_list(sp.get("blacklist",""))
        if wl: fields["link_whitelist"]   = wl
        if bl: fields["domain_blacklist"] = bl
        act=(sp.get("action") or '').strip().lower();
        if act in ("delete","warn","mute"): fields["action_primary"] = act
        lim=str(sp.get("per_user_daily_limit") or '').strip();
        if lim.isdigit(): fields["per_user_daily_limit"] = int(lim)
        qn=(sp.get("quota_notify") or '').strip().lower();
        if qn in ("off","smart","always"): fields["quota_notify"]=qn
        if topic_id is not None and fields:
            db["set_spam_policy_topic"](cid, topic_id, **fields)
    except Exception as e: errors.append(f"Spam/Links: {e}")

    # RSS: add/del + update (enabled/post_images)
    try:
        r = data.get("rss") or {}
        if (r.get("url") or '').strip():
            url = r.get("url").strip(); topic = int(r.get("topic") or 0)
            try: db["set_rss_topic"](cid, topic)
            except Exception: pass
            db["add_rss_feed"](cid, url, topic)
            db["set_rss_feed_options"](cid, url, post_images=bool(r.get("post_images")), enabled=bool(r.get("enabled", True)))
        upd = data.get("rss_update") or None
        if upd and (upd.get("url") or '').strip():
            url=upd.get("url").strip()
            db["set_rss_feed_options"](cid, url, post_images=upd.get("post_images"), enabled=upd.get("enabled"))
        if data.get("rss_del"): db["remove_rss_feed"](cid, data.get("rss_del"))
    except Exception as e: errors.append(f"RSS: {e}")

    # KI (Assistent/FAQ)
    try:
        ai = data.get("ai") or {}
        faq_on = bool(ai.get("on") or (ai.get("faq") or '').strip())
        db["set_ai_settings"](cid, faq=faq_on, rss=None)
    except Exception as e: errors.append(f"KI: {e}")

    # FAQ
    try:
        faq_add = data.get("faq_add") or None
        if faq_add and (faq_add.get("q") or '').strip():
            db["upsert_faq"](cid, faq_add["q"].strip(), (faq_add.get("a") or '').strip())
        faq_del = data.get("faq_del") or None
        if faq_del and (faq_del.get("q") or '').strip():
            db["delete_faq"](cid, faq_del["q"].strip())
    except Exception as e: errors.append(f"FAQ: {e}")

    # KI‑Moderation (viele Felder erlaubt)
    try:
        aimod = data.get("ai_mod") or {}
        if aimod:
            # Leere Strings zu None konvertieren!
            aimod_clean = _clean_dict_empty_to_none(aimod)
            allowed = {
              "enabled","shadow_mode","action_primary","mute_minutes","warn_text","appeal_url",
              "max_per_min","cooldown_s","exempt_admins","exempt_topic_owner",
              "toxicity","hate","sexual","harassment","selfharm","violence",
              # Aliase → DB‑Spalten
              "tox_thresh","hate_thresh","sex_thresh","harass_thresh","selfharm_thresh","violence_thresh"
            }
            payload={}
            for k in allowed:
                if k in aimod_clean and aimod_clean[k] is not None:
                    payload[k]=aimod_clean[k]
            # Aliase umbenennen
            alias = {
              "toxicity":"tox_thresh","hate":"hate_thresh","sexual":"sex_thresh",
              "harassment":"harass_thresh","selfharm":"selfharm_thresh","violence":"violence_thresh"
            }
            for k,v in list(payload.items()):
              if k in alias: payload[alias[k]]=v; del payload[k]
            db["set_ai_mod_settings"](cid, 0, **payload)
    except Exception as e: errors.append(f"AI‑Mod: {e}")

    # Daily Report
    try:
        if "daily_stats" in data: db["set_daily_stats"](cid, bool(data.get("daily_stats")))
    except Exception as e: errors.append(f"Daily‑Report: {e}")

    # Mood
    try:
        if (data.get("mood") or {}).get("question", "").strip(): db["set_mood_question"](cid, data["mood"]["question"].strip())
        if str((data.get("mood") or {}).get("topic", "")).strip().isdigit(): db["set_mood_topic"](cid, int(str(data["mood"]["topic"]).strip()))
    except Exception as e: errors.append(f"Mood: {e}")

    # Sprache
    try:
        lang=(data.get("language") or '').strip()
        if lang: db["set_group_language"](cid, lang[:5])
    except Exception as e: errors.append(f"Sprache: {e}")

    # Nachtmodus (erweitert)
    try:
        night = data.get("night") or {}
        if ("on" in night) or ("start" in night) or ("end" in night) or ("timezone" in night):
            def _hm_to_min(s, default):
                try:
                    h,m = str(s or '').split(':'); return int(h)*60 + int(m)
                except Exception: return default
            enabled = bool(night.get("on"))
            start_m = _hm_to_min(night.get("start") or "22:00", 1320)
            end_m   = _hm_to_min(night.get("end") or "07:00", 360)
            # Leere Strings zu None für override_until
            override_until = night.get("override_until")
            if isinstance(override_until, str) and override_until.strip() == "":
                override_until = None
            db["set_night_mode"](cid,
                enabled=enabled,
                start_minute=start_m,
                end_minute=end_m,
                delete_non_admin_msgs = night.get("delete_non_admin_msgs"),
                warn_once = night.get("warn_once"),
                timezone = night.get("timezone"),
                hard_mode = night.get("hard_mode"),
                override_until = override_until
            )
    except Exception as e: errors.append(f"Nachtmodus: {e}")

    # Router
    try:
        if "router_add" in data:
            ra = data["router_add"] or {}
            target = int(ra.get("target_topic_id") or 0)
            kw = ra.get("keywords") or []
            dom = ra.get("domains") or []
            del_orig = bool(ra.get("delete_original", True))
            warn_user = bool(ra.get("warn_user", True))
            if target:
                db["add_topic_router_rule"](cid, target, keywords=kw, domains=dom, delete_original=del_orig, warn_user=warn_user)
    except Exception as e: errors.append(f"Router: {e}")

    # Pro kaufen/verlängern
    try:
        months = int(data.get("pro_months") or 0)
        if months>0:
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo
            until = datetime.now(ZoneInfo("UTC")) + timedelta(days=30*months)
            db["set_pro_until"](cid, until, tier="pro")
    except Exception as e: errors.append(f"Pro‑Abo: {e}")

    if errors:
        return await msg.reply_text("⚠️ Teilweise gespeichert:\n• " + "\n• ".join(errors))
    return await msg.reply_text("✅ Einstellungen gespeichert.")

async def _cors_ok(request):
    # Einheitliche Antwort für Preflight
    return web.json_response(
        {}, status=204,
        headers={
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
        }
    )


def _attach_http_routes(app: Application) -> bool:
    """Versucht, die HTTP-Routen am PTB-aiohttp-Webserver zu registrieren.
    Gibt True zurück, wenn registriert (oder bereits vorhanden), sonst False.
    """
    try:
        webapp = app.webhook_application()
    except Exception:
        webapp = None

    if not webapp:
        logger.info("[miniapp] webhook_application() noch nicht verfügbar – retry folgt")
        return False

    # Doppelte Registrierung vermeiden:
    if webapp.get("_miniapp_routes_attached"):
        return True

    webapp.setdefault("_ptb_apps", [])
    webapp["_ptb_apps"].append(app)
    webapp.setdefault("ptb_app", app)

def register_miniapp_routes(webapp, app):
    ALLOWED_ORIGIN = "https://greeny187.github.io"
    async def _cors_ok(_request):
        return web.json_response({}, status=204, headers={
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
        })

    webapp.setdefault("_ptb_apps", [])
    webapp["_ptb_apps"].append(app)
    webapp.setdefault("ptb_app", app)

    # GET
    webapp.router.add_route("GET",     "/miniapp/state",     route_state)
    webapp.router.add_route("GET",     "/miniapp/stats",     route_stats)
    webapp.router.add_route("GET", "/miniapp/file", route_file)
    webapp.router.add_route("GET",     "/miniapp/send_mood", route_send_mood)
    # POST
    webapp.router.add_route("POST",    "/miniapp/apply",     route_apply)
    # OPTIONS (CORS)
    for p in ("/miniapp/state","/miniapp/stats","/miniapp/file","/miniapp/send_mood","/miniapp/apply"):
        webapp.router.add_route("OPTIONS", p, _cors_ok)

    webapp["_miniapp_routes_attached"] = True
    logger.info("[miniapp] HTTP-Routen registriert")
    return True

def register_miniapp(app: Application):
    # Bot-Token dynamisch sammeln (für die Init-Data-Verifikation)
    try:
        tok = getattr(app.bot, "token", None)
        if tok:
            _ALL_TOKENS.add(tok)
    except Exception:
        pass
    
    # 1) Handler wie gehabt
    app.add_handler(CommandHandler("miniapp", miniapp_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, webapp_data_handler, block=False), group=-4)

