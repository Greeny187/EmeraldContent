﻿import os
import datetime
import re
import logging
import random
import time, telegram
from collections import deque
from urllib.parse import urlparse
from datetime import date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, ForceReply, ChatPermissions
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, ChatMemberHandler, CallbackQueryHandler
from telegram.error import BadRequest, Forbidden
from telegram.constants import ChatType, ChatMemberStatus
try:
    from .user_manual import help_handler  # falls du das im /help verwendest
except Exception:
    async def help_handler(update, context):
        await update.effective_message.reply_text("Hilfe ist aktuell nicht hinterlegt.")

# Access-Helfer (Admin/Owner/Topic-Owner-Ermittlung)
from .access import resolve_privileged_flags, get_visible_groups, cached_admins, is_admin_or_owner

# DB-Import robust halten (Monorepo vs. Standalone)

from .database import (register_group, get_registered_groups, get_rules, set_welcome, set_rules, set_farewell, add_member, get_link_settings, 
    remove_member, inc_message_count, assign_topic, remove_topic, has_topic, set_mood_question, get_farewell, get_welcome, get_captcha_settings,
    get_night_mode, set_night_mode, get_group_language, set_spam_policy_topic, get_spam_policy_topic,
    add_topic_router_rule, list_topic_router_rules, delete_topic_router_rule, get_effective_link_policy, is_pro_chat,
    toggle_topic_router_rule, get_matching_router_rule, upsert_forum_topic, rename_forum_topic, find_faq_answer, log_auto_response, get_ai_settings,
    effective_spam_policy, count_topic_user_messages_today, 
    effective_ai_mod_policy, log_ai_mod_action, count_ai_hits_today, add_strike_points, get_strike_points, top_strike_users, decay_strikes
    )
from zoneinfo import ZoneInfo
from .patchnotes import __version__, PATCH_NOTES
from .utils import (clean_delete_accounts_for_chat, ai_summarize, 
    ai_available, ai_moderate_text, ai_moderate_image, _extract_domains_from_text, 
    heuristic_link_risk, _apply_hard_permissions)
from .statistic import log_spam_event, log_night_event
from shared.translator import translate_hybrid

logger = logging.getLogger(__name__)

_EMOJI_RE = re.compile(r'([\U0001F300-\U0001FAFF\U00002600-\U000027BF])')
_URL_RE = re.compile(r'(https?://\S+|www\.\S+)', re.IGNORECASE)

async def _on_admin_change(update, context):
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is not None:
        context.bot_data.get("admins_cache", {}).pop(chat_id, None)

def _count_emojis(text:str) -> int:
    return len(_EMOJI_RE.findall(text or ""))

def _bump_rate(context, chat_id:int, user_id:int):
    key = ("rl", chat_id, user_id)
    now = time.time()
    q = context.chat_data.get(key, [])
    q = [t for t in q if now - t < 10.0]  # sliding window 10s
    q.append(now)
    context.chat_data[key] = q
    return len(q)  # messages in last 10s

def tr(text: str, lang: str) -> str:
    return translate_hybrid(text, target_lang=lang)

async def _is_admin(context, chat_id: int, user_id: int) -> bool:
    """True, wenn user_id Admin/Owner ist – nutzt den CM-Cache."""
    try:
        is_admin, is_owner = await is_admin_or_owner(context.bot, chat_id, user_id, context=context)
        return bool(is_admin or is_owner)
    except Exception:
        return False

async def safe_send_welcome(bot, db, chat_id, text, topic_id, file_id, parse_mode=None):
    try:
        kwargs = {"parse_mode": parse_mode}
        if topic_id is not None:
            kwargs["message_thread_id"] = topic_id

        if file_id:
            await bot.send_photo(chat_id, file_id, caption=text, **kwargs)
        else:
            await bot.send_message(chat_id, text, **kwargs)
        return

    except BadRequest as e:
        msg = str(e).lower()

        # Thread ungültig -> ohne thread erneut senden & DB heilen
        if "message_thread" in msg:
            if file_id:
                await bot.send_photo(chat_id, file_id, caption=text, parse_mode=parse_mode)
            else:
                await bot.send_message(chat_id, text, parse_mode=parse_mode)
            try:
                db.clear_welcome_topic(chat_id)  # kleiner DB-Helper: setzt welcome_topic_id=NULL
            except Exception:
                pass
            return

        # Ungültiges file_id -> zumindest Text senden & Medienfelder leeren
        if "wrong file identifier" in msg or "file not found" in msg:
            await bot.send_message(chat_id, text, parse_mode=parse_mode)
            try:
                db.clear_welcome_media(chat_id)  # setzt welcome_file_id/image_url=NULL
            except Exception:
                pass
            return

        raise

def _is_anon_admin_message(msg) -> bool:
    """True, wenn Nachricht als anonymer Admin (sender_chat == chat) kam."""
    try:
        return bool(getattr(msg, "sender_chat", None) and msg.sender_chat.id == msg.chat.id)
    except Exception:
        return False

async def _resolve_username_to_user(context, chat_id: int, username: str):
    """
    Versucht @username → telegram.User aufzulösen:
    1) Aus context.chat_data['username_map']
    2) Fallback: aus gecachter Adminliste (cached_admins)
    """
    name = username.lstrip("@").lower()

    # 1) Chat-Map
    try:
        umap = context.chat_data.get("username_map") or {}
        uid = umap.get(name)
        if uid:
            member = await context.bot.get_chat_member(chat_id, uid)
            return member.user
    except Exception:
        pass

    # 2) Admin-Cache
    try:
        admins = await cached_admins(context.bot, context, chat_id)
        for a in admins:
            if a.user.username and a.user.username.lower() == name:
                return a.user
    except Exception:
        pass

    return None

def _is_quiet_now(start_min: int, end_min: int, now_min: int) -> bool:
    # Fenster Ã¼ber Mitternacht: start > end -> quiet wenn now >= start oder now < end
    if start_min == end_min:
        return False  # 0-LÃ¤nge Fenster
    if start_min < end_min:
        return start_min <= now_min < end_min
    else:
        return now_min >= start_min or now_min < end_min
    
def _aimod_acquire(context, chat_id:int, max_per_min:int) -> bool:
    key = ("aimod_rate", chat_id)
    now = time.time()
    q = [t for t in context.bot_data.get(key, []) if now - t < 60.0]
    if len(q) >= max_per_min:
        context.bot_data[key] = q
        return False
    q.append(now); context.bot_data[key] = q
    return True

def _parse_hhmm(txt: str) -> int | None:
    m = re.match(r'^\s*(\d{1,2}):(\d{2})\s*$', txt)
    if not m: 
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if 0 <= hh < 24 and 0 <= mm < 60:
        return hh*60 + mm
    return None

def _already_seen(context, chat_id: int, message_id: int) -> bool:
    dq = context.chat_data.get("mod_seen")
    if dq is None:
        dq = context.chat_data["mod_seen"] = deque(maxlen=1000)
    key = (chat_id, message_id)
    if key in dq:
        return True
    dq.append(key)
    return False

def _once(context, key: tuple, ttl: float = 5.0) -> bool:
    now = time.time()
    bucket = context.chat_data.get("once") or {}
    last = bucket.get(key)
    if last and (now - last) < ttl:
        return False
    bucket[key] = now
    context.chat_data["once"] = bucket
    return True

async def _safe_delete(msg):
    try:
        await msg.delete()
        return True
    except BadRequest as e:
        s = str(e).lower()
        if "can't be deleted" in s or "message to delete not found" in s:
            return False
        raise

async def _hard_delete_message(context, chat_id: int, msg) -> bool:
    """
    LÃ¶scht eine Nachricht robust:
    1) msg.delete()
    2) bot.delete_message(chat_id, message_id)
    Gibt True zurÃ¼ck, wenn gelÃ¶scht; sonst False (loggt Ursache).
    """
    try:
        await msg.delete()
        return True
    except (BadRequest, Forbidden) as e1:
        logger.warning(f"msg.delete() failed in {chat_id}: {e1}")
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            return True
        except (BadRequest, Forbidden) as e2:
            logger.error(f"bot.delete_message() failed in {chat_id}: {e2}")
            return False
    except Exception as e:
        logger.exception(f"Unexpected delete error in {chat_id}: {e}")
        return False

async def spam_enforcer(update, context):
    msg = update.effective_message
    if not msg:
        return

    chat = update.effective_chat
    user = update.effective_user
    chat_id = chat.id
    text = msg.text or msg.caption or ""
    topic_id = getattr(msg, "message_thread_id", None)

    if _already_seen(context, chat_id, msg.message_id):
        return

    # --- FIX: Variablen deterministisch initialisieren ---
    privileged = False
    is_topic_owner = False

    # Privilegien ermitteln (Admin/Owner/anon)
    is_owner, is_admin, is_anon_admin, is_topic_owner_flag, _, _ = \
        await resolve_privileged_flags(msg, context)

    # Topic-Owner (DB) ergänzen
    if topic_id and user:
        try:
            is_topic_owner = is_topic_owner_flag or has_topic(chat.id, user.id, topic_id)
        except Exception:
            is_topic_owner = is_topic_owner_flag

    # Ab jetzt ist privileged IMMER definiert
    privileged = bool(is_owner or is_admin or is_anon_admin or is_topic_owner)

    # Policy JETZT laden (vor jeglicher Nutzung)
    policy = get_effective_link_policy(chat_id, topic_id) or {}

    # Exemptions
    if (is_topic_owner and policy.get("exempt_topic_owner", True)) or (privileged and policy.get("exempt_admins", True)):
        return

    # Domains zuverlässig extrahieren (richtige Utils-Funktion!)
    domains_in_msg = _extract_domains_from_text(text)
    violation = False
    reason = None
    if domains_in_msg:
        bl = set((policy.get("blacklist") or []))
        wl = set((policy.get("whitelist") or []))
        # Blacklist
        if any(h.endswith("."+d) or h == d for d in bl for h in domains_in_msg):
            reason = "domain_blacklist"
            deleted = await _safe_delete(msg)
            did = "delete" if deleted else "none"
            # Aktion (mute optional)
            act = (policy.get("action") or "delete").lower()
            if act == "mute" and not is_admin and user:
                try:
                    until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
                    await context.bot.restrict_chat_member(
                        chat_id, user.id,
                        permissions=ChatPermissions(can_send_messages=False),
                        until_date=until
                    )
                    did += "/mute60m"
                except Exception as e:
                    logger.warning(f"mute failed: {e}")
            if _once(context, ("link_warn", chat_id, (user.id if user else 0)), ttl=5.0):
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        message_thread_id=topic_id,
                        text=policy.get("warning_text") or "🚫 Nur Admins dürfen Links posten."
                    )
                except Exception:
                    pass
            try:
                log_spam_event(chat_id, user.id if user else None, reason, did, {"domains": domains_in_msg})
            except Exception:
                pass
            return

        # Nur-Admin-Links (Whitelist erlaubt)
        if policy.get("admins_only") and not is_admin:
            def allowed(host): return any(host.endswith("."+d) or host == d for d in wl)
            if not any(allowed(h) for h in domains_in_msg):
                deleted = await _safe_delete(msg)
                if _once(context, ("link_warn", chat_id, (user.id if user else 0)), ttl=5.0):
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            message_thread_id=topic_id,
                            text=policy.get("warning_text") or "🚫 Nur Admins dürfen Links posten."
                        )
                    except Exception:
                        pass
                try:
                    log_spam_event(chat_id, user.id if user else None, "admins_only", "delete" if deleted else "none", {"domains": domains_in_msg})
                except Exception:
                    pass
                return

    # --- QUOTA / FLOOD (pro Topic & User) ---
    link_flags  = get_link_settings(chat_id)
    spam_pol    = effective_spam_policy(chat_id, topic_id, link_flags)
    daily_lim   = int(spam_pol.get("per_user_daily_limit") or 0)
    notify_mode = (spam_pol.get("quota_notify") or "smart").lower()

    if topic_id and daily_lim > 0 and user and not privileged:
        used_before = count_topic_user_messages_today(chat_id, topic_id, user.id, tz="Europe/Berlin")
        if used_before >= daily_lim:
            deleted = await _hard_delete_message(context, chat_id, msg)
            did_action = "delete" if deleted else "none"
            if (spam_pol.get("action_primary","delete").lower() in ("mute","stumm")):
                try:
                    until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
                    await context.bot.restrict_chat_member(chat_id, user.id, ChatPermissions(can_send_messages=False), until_date=until)
                    did_action = (did_action + "/mute60m") if did_action != "none" else "mute60m"
                except Exception as e:
                    logger.warning(f"Limit mute failed in {chat_id}: {e}")
            try:
                await context.bot.send_message(chat_id=chat_id, message_thread_id=topic_id,
                                               text=f"🛑 Tageslimit erreicht ({daily_lim}) – bitte morgen weiter.")
            except Exception:
                pass
            try:
                log_spam_event(chat_id, user.id, "limit_day", did_action,
                               {"limit": daily_lim, "used_before": used_before, "topic_id": topic_id})
            except Exception:
                pass
            return

        remaining_after = daily_lim - (used_before + 1)
        if notify_mode == "always" or (notify_mode == "smart" and (used_before in (0,) or remaining_after in (10,5,2,1,0))):
            try:
                await context.bot.send_message(chat_id=chat_id, message_thread_id=topic_id,
                                               reply_to_message_id=msg.message_id,
                                               text=f"🧮 Rest heute: {max(remaining_after,0)}/{daily_lim}")
            except Exception:
                pass

    # 3) Emoji- und Flood-Limits (je nach Level/Override)
    if not privileged:
        em_lim = policy.get("emoji_max_per_msg") or 0
        if em_lim > 0:
            emc = _count_emojis(text)
            if emc > em_lim:
                try:
                    await msg.delete()
                    log_spam_event(chat_id, user.id if user else None, "emoji_per_msg", "delete",
                                   {"count": emc, "limit": em_lim})
                except Exception: pass
                return

        flood_lim = policy.get("max_msgs_per_10s") or 0
        if flood_lim > 0:
            n = _bump_rate(context, chat_id, user.id if user else 0)
            if n > flood_lim:
                try:
                    await msg.delete()
                    log_spam_event(chat_id, user.id if user else None, "flood_10s", "delete",
                                   {"count_10s": n, "limit": flood_lim})
                except Exception: pass
                return

async def ai_moderation_enforcer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or chat.type not in ("group","supergroup"): return
    text = msg.text or msg.caption or ""
    if not text:  # (optional) Medien/OCR kÃ¶nntest du spÃ¤ter ergÃ¤nzen
        return

    topic_id = getattr(msg, "message_thread_id", None)
    # Pro-Gate: KI-Moderation nur in Pro-Gruppen
    if not is_pro_chat(chat.id):
        return
    policy = effective_ai_mod_policy(chat.id, topic_id)
    
    if not policy.get("enabled"):
        return

    # Privilegien
    is_admin = await _is_admin(context, chat.id, user.id if user else 0)
    is_topic_owner = False  # falls du Topic-Owner-Check hast: hier einsetzen
    if (is_admin and policy.get("exempt_admins", True)) or (is_topic_owner and policy.get("exempt_topic_owner", True)):
        return

    # Rate-Limit / Cooldown
    if not _aimod_acquire(context, chat.id, int(policy.get("max_calls_per_min", 20))):
        return
    # optionale Cooldown pro Chat: einfache Sperre (letzte Aktion)
    cd_key = ("aimod_cooldown", chat.id)
    last_t = context.bot_data.get(cd_key)
    if last_t and time.time() - last_t < int(policy.get("cooldown_s", 30)):
        return

    # Domains & Link-Risiko
    domains = _extract_domains_from_text(text)
    link_score = heuristic_link_risk(domains)

    # Moderation (AI)
    scores = {"toxicity":0,"hate":0,"sexual":0,"harassment":0,"selfharm":0,"violence":0}
    flagged = False
    
    media_scores = None
    media_kind = None
    file_id = None

    if msg.photo:
        media_kind = "photo"
        file_id = msg.photo[-1].file_id
    elif msg.sticker:
        media_kind = "sticker"
        file_id = msg.sticker.file_id
    elif msg.animation:  # GIF
        if getattr(msg.animation, "thumbnail", None):
            media_kind = "animation_thumb"
            file_id = msg.animation.thumbnail.file_id
    elif msg.video:
        if getattr(msg.video, "thumbnail", None):
            media_kind = "video_thumb"
            file_id = msg.video.thumbnail.file_id

    if file_id and ai_available():
        try:
            f = await context.bot.get_file(file_id)
            img_url = f.file_path  # Telegram CDN URL
            media_scores = await ai_moderate_image(img_url) or {}
        except Exception:
            media_scores = None
    
    if ai_available():
        res = await ai_moderate_text(text, model=policy.get("model","omni-moderation-latest"))
        if res:
            scores.update(res.get("categories") or {})
            flagged = bool(res.get("flagged"))

    # Entscheidung
    violations = []
    if scores["toxicity"]   >= policy["tox_thresh"]:     violations.append(("toxicity", scores["toxicity"]))
    if scores["hate"]       >= policy["hate_thresh"]:    violations.append(("hate", scores["hate"]))
    if scores["sexual"]     >= policy["sex_thresh"]:     violations.append(("sexual", scores["sexual"]))
    if scores["harassment"] >= policy["harass_thresh"]:  violations.append(("harassment", scores["harassment"]))
    if scores["selfharm"]   >= policy["selfharm_thresh"]:violations.append(("selfharm", scores["selfharm"]))
    if scores["violence"]   >= policy["violence_thresh"]:violations.append(("violence", scores["violence"]))
    if link_score           >= policy["link_risk_thresh"]: violations.append(("link_risk", link_score))
    if media_scores:
        if media_scores.get("nudity",0) >= policy["visual_nudity_thresh"]:
            violations.append(("nudity", float(media_scores["nudity"])))
        if policy.get("block_sexual_minors", True) and media_scores.get("sexual_minors",0) >= 0.01:
            violations.append(("sexual_minors", float(media_scores["sexual_minors"])))
        if media_scores.get("violence",0) >= policy["visual_violence_thresh"]:
            violations.append(("violence_visual", float(media_scores["violence"])))
        if media_scores.get("weapons",0) >= policy["visual_weapons_thresh"]:
            violations.append(("weapons", float(media_scores["weapons"])))
        if media_scores.get("gore",0) >= policy["visual_violence_thresh"]:
            violations.append(("gore", float(media_scores["gore"])))
    if not violations:
        if policy.get("shadow_mode"):
            log_ai_mod_action(chat.id, topic_id, user.id if user else None, msg.message_id,
                              "ok", 0.0, "allow",
                              {"text_scores":scores, "media_scores":media_scores, "link_score":link_score})
        return

    if policy.get("shadow_mode"):
        log_ai_mod_action(chat.id, topic_id, user.id if user else None, msg.message_id,
                          violations[0][0], float(violations[0][1]), "shadow",
                          {"text_scores":scores, "media_scores":media_scores, "domains":domains, "link_score":link_score})
        return

    # PrimÃ¤raktion + Eskalation (heutige Treffer)
    action = policy.get("action_primary","delete")
    hits_today = count_ai_hits_today(chat.id, user.id if user else 0)
    if hits_today + 1 >= int(policy.get("escalate_after",3)):
        action = policy.get("escalate_action","mute")

    # STRIKES: Punkte vergeben (Schwere je Kategorie)
    severity = {
        "toxicity":1,"hate":2,"sexual":2,"harassment":1,"selfharm":2,"violence":2,"link_risk":1,
        "nudity":2,"sexual_minors":5,"violence_visual":2,"weapons":2,"gore":3
    }
    strike_points = max(1, int(policy.get("strike_points_per_hit",1)))
    main_cat = violations[0][0]
    multi = severity.get(main_cat, 1)
    total_points = strike_points * multi
    try:
        if user:
            add_strike_points(chat.id, user.id, total_points, reason=main_cat)
    except Exception:
        pass

    # Strike-Eskalation (persistente Punkte)
    strikes = get_strike_points(chat.id, user.id if user else 0)
    if strikes >= int(policy.get("strike_ban_threshold",5)):
        action = "ban"
    elif strikes >= int(policy.get("strike_mute_threshold",3)) and action != "ban":
        action = "mute"

    warn_text = policy.get("warn_text") or "âš ï¸ Inhalt entfernt (KI-Moderation)."
    appeal_url = policy.get("appeal_url")

    try:
        # Delete (falls sinnvoll fÃ¼r alle Aktionsarten)
        try: await msg.delete()
        except: pass

        # Warnen
        txt = warn_text
        if appeal_url: txt += f"\n\nWiderspruch: {appeal_url}"
        await context.bot.send_message(chat_id=chat.id, message_thread_id=topic_id, text=txt)

        # mute/ban
        if action in ("mute","ban") and user:
            try:
                if action == "ban":
                    await context.bot.ban_chat_member(chat.id, user.id)
                else:
                    until = datetime.datetime.utcnow() + datetime.timedelta(minutes=int(policy.get("mute_minutes",60)))
                    perms = telegram.ChatPermissions(can_send_messages=False)
                    await context.bot.restrict_chat_member(chat.id, user.id, permissions=perms, until_date=until)
            except Exception:
                pass

        context.bot_data[("aimod_cooldown", chat.id)] = time.time()
        log_ai_mod_action(chat.id, topic_id, user.id if user else None, msg.message_id,
                          main_cat, float(violations[0][1]), action,
                          {"text_scores":scores, "media_scores":media_scores, "domains":domains, "link_score":link_score, "strikes":strikes, "added_points":total_points})
    except Exception as e:
        log_ai_mod_action(chat.id, topic_id, user.id if user else None, msg.message_id, "error", 0.0, "error", {"err":str(e)})

async def mystrikes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; user = update.effective_user
    pts = get_strike_points(chat.id, user.id)
    await update.effective_message.reply_text(f"Du hast aktuell {pts} Strike-Punkte.")

async def strikes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    # /strikes -> Topliste
    rows = top_strike_users(chat.id, limit=10)
    if not rows: 
        return await update.effective_message.reply_text("Keine Strikes vorhanden.")
    lines = []
    for uid, pts in rows:
        lines.append(f"â€¢ {uid}: {pts} Pkt")
    await update.effective_message.reply_text("Top-Strikes:\n" + "\n".join(lines))

async def faq_autoresponder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    text = (msg.text or msg.caption or "")
    if not msg or not chat or chat.type not in ("group","supergroup") or not text:
        return

    logger.debug(f"[FAQ] enter chat={chat.id} mid={msg.message_id} has_text={bool(text)}")

    # Heuristik, nur echte Fragen o. explizite FAQ-Trigger
    if "?" not in text and not text.lower().startswith(("faq ", "/faq ")):
        return

    t0 = time.time()
    hit = find_faq_answer(chat.id, text)
    if hit:
        trig, ans = hit
        await msg.reply_text(ans, parse_mode="HTML")
        log_auto_response(chat.id, trig, 1.0, ans[:200], int((time.time()-t0)*1000), None)
        return

    ai_faq, _ = get_ai_settings(chat.id)
    if not ai_faq or not is_pro_chat(chat.id):
        return

    lang = get_group_language(chat.id) or "de"
    context_info = (
        "Support: https://t.me/EmeraldContentSupport • "
    )
    prompt = f"Frage: {text}\n\n{context_info}\n\nAntworte knapp (2–3 Sätze) auf {lang}."
    try:
        answer = await ai_summarize(prompt, lang=lang)
    except Exception:
        answer = None
    if answer:
        await msg.reply_text(answer, parse_mode="HTML")
        log_auto_response(chat.id, "AI", 0.5, answer[:200], int((time.time()-t0)*1000), None)

async def nightmode_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flag = context.user_data.get('awaiting_nm_time')
    if not flag:
        return
    kind, cid = flag
    lang = get_group_language(cid) or 'de'
    txt = (update.effective_message.text or "").strip()
    val = _parse_hhmm(txt)
    if val is None:
        return await update.effective_message.reply_text(tr("⚠️ Bitte im Format HH:MM senden, z. B. 22:00.", lang))
    if kind == 'start':
        set_night_mode(cid, start_minute=val)
        await update.effective_message.reply_text(tr("âœ… Startzeit gespeichert:", lang) + f" {txt}")
    else:
        set_night_mode(cid, end_minute=val)
        await update.effective_message.reply_text(tr("âœ… Endzeit gespeichert:", lang) + f" {txt}")
    context.user_data.pop('awaiting_nm_time', None)

def _parse_duration(s: str) -> datetime.timedelta | None:
    s = (s or "").strip().lower()
    if not s:
        return None
    m = re.match(r'^(\d+)\s*([hm])$', s)
    if not m:
        return None
    val = int(m.group(1))
    unit = m.group(2)
    return datetime.timedelta(hours=val) if unit == 'h' else datetime.timedelta(minutes=val)

async def quietnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat  # Add this line to define chat
    lang = get_group_language(chat.id) or 'de'
    if chat.type not in ("group","supergroup"):
        return await update.message.reply_text(tr("Bitte im Gruppenchat verwenden.", lang))

    # Admin-Gate
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        if update.effective_user.id not in {a.user.id for a in admins}:
            return await update.message.reply_text(tr("Nur Admins dÃ¼rfen die Ruhephase starten.", lang))
    except Exception:
        pass

    args = context.args or []
    dur = _parse_duration(args[0]) if args else datetime.timedelta(hours=8)
    if not dur:
        return await update.message.reply_text(tr("Format: /quietnow 30m oder /quietnow 2h", lang))

    en, s, e, del_non_admin, warn_once, tz, hard_mode, _ = get_night_mode(chat.id)
    now = datetime.datetime.now(ZoneInfo(tz))
    until = now + dur
    set_night_mode(chat.id, override_until=until)
    try:
        log_night_event(chat.id, "quietnow", 1, until_ts=until.astimezone(datetime.timezone.utc))
    except Exception:
        pass

    if hard_mode:
        # sofort sperren
        await _apply_hard_permissions(context, chat.id, True)
        context.chat_data.setdefault("nm_flags", {})["hard_applied"] = True

    human = until.strftime("%H:%M")
    await update.message.reply_text(tr("ðŸŒ™ Sofortige Ruhephase aktiv bis", lang) + f" {human} ({tz}).")

async def error_handler(update, context):
    """Fängt alle nicht abgefangenen Errors auf, loggt und benachrichtigt Telegram-Dev-Chat."""
    logger.error("Uncaught exception", exc_info=context.error)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type in ("group", "supergroup"):
        register_group(chat.id, chat.title)
        return await update.message.reply_text(
            "👋 Willkommen beim *Emerald Content Bot*!\n\n"
            "Ich helfe dir, deine Telegram-Gruppe automatisch zu verwalten "
            "inklusive Schutz, Statistiken, Rollenverwaltung, Captcha u.v.m.\n\n"
            "❓ Mehr Infos: [Zur Website](https://greeny187.github.io/EmeraldContent/)\n\n"
            "🎁 *Unterstütze das Projekt:*\n"
            "• TON Wallet: `UQBopac1WFJGC_K48T8JqcbRoH3evUoUDwS2oItlS-SgpR8L`\n"
            "• PayPal: emerald@mail.de\n\n"
            "✅ Gruppe registriert! Geh privat auf /miniapp.")

    if chat.type == "private":
        all_groups = get_registered_groups()
        visible_groups = await get_visible_groups(user.id, context.bot, all_groups)

        if not visible_groups:
            return await update.message.reply_text(
                "ðŸš« Du bist in keiner Gruppe Admin, in der der Bot aktiv ist.\n"
                "âž• FÃ¼ge den Bot in eine Gruppe ein und gib ihm Adminrechte."
            )

        keyboard = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid, title in visible_groups]
        markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("ðŸ”§ WÃ¤hle eine Gruppe:", reply_markup=markup)

async def forum_topic_registry_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or chat.type != "supergroup":  # Topics gibt's in Foren-Supergroups
        return

    tid = getattr(msg, "message_thread_id", None)
    if tid:
        # normaler Beitrag in einem Topic -> last_seen updaten
        upsert_forum_topic(chat.id, tid, None)

    # Topic erstellt/editiert? (Service-Messages)
    ftc = getattr(msg, "forum_topic_created", None)
    if ftc and tid:
        upsert_forum_topic(chat.id, tid, getattr(ftc, "name", None) or None)

    fte = getattr(msg, "forum_topic_edited", None)
    if fte and tid and getattr(fte, "name", None):
        rename_forum_topic(chat.id, tid, fte.name)

async def version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Version {__version__}\n\nPatchnotes:\n{PATCH_NOTES}")

async def message_logger(update, context):
    logger.info(f"ðŸ’¬ message_logger aufgerufen in Chat {update.effective_chat.id}")
    msg = update.effective_message
    if msg.chat.type in ("group", "supergroup") and msg.from_user:
        inc_message_count(msg.chat.id, msg.from_user.id, date.today())
        # neu: stelle sicher, dass jeder Schreiber in die members-Tabelle kommt
        try:
            add_member(msg.chat.id, msg.from_user.id)
            logger.info(f"âž• add_member via message_logger: chat={msg.chat.id}, user={msg.from_user.id}")
        except Exception as e:
            logger.info(f"Fehler add_member in message_logger: {e}", exc_info=True)

        # ðŸ”¹ NEU: Usernameâ†’ID Map im Chat pflegen (fÃ¼r @username-AuflÃ¶sung)
        try:
            if msg.from_user.username:
                m = context.chat_data.get("username_map") or {}
                m[msg.from_user.username.lower()] = msg.from_user.id
                context.chat_data["username_map"] = m
        except Exception:
            pass
        
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Schlanker Fallback:
    - Niemals in KanÃ¤len laufen
    - Mood-Frage beantworten
    - Sonst: nichts tun
    """
    msg   = update.effective_message
    chat  = update.effective_chat
    ud    = context.user_data or {}

    # Nur Privat/Gruppe/Supergruppe â€“ KanÃ¤le explizit ausschlieÃŸen
    if chat.type not in (ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP):
        return

    # 1) Expliziter Mini-Flow: Mood-Frage
    if ud.get("awaiting_mood_question"):
        return await mood_question_handler(update, context)

    # 3) Sonst: nichts â€“ andere Aufgaben haben eigene Handler
    return

async def edit_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Nur aktiv, wenn zuvor im MenÃ¼ â€žBearbeitenâ€œ gedrÃ¼ckt wurde
    if "last_edit" not in context.user_data:
        return

    chat_id, action = context.user_data.pop("last_edit")
    msg = update.message

    # Foto + Caption oder reiner Text
    if msg.photo:
        photo_id = msg.photo[-1].file_id
        text = msg.caption or ""
    else:
        photo_id = None
        text = msg.text or ""

    # In DB schreiben
    if action == "welcome_edit":
        set_welcome(chat_id, photo_id, text)
        label = "BegrÃ¼ÃŸung"
    elif action == "rules_edit":
        set_rules(chat_id, photo_id, text)
        label = "Regeln"
    elif action == "farewell_edit":
        set_farewell(chat_id, photo_id, text)
        label = "Farewell-Nachricht"
    else:
        return

    # BestÃ¤tigung mit ZurÃ¼ck-Button ins MenÃ¼
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("â¬… ZurÃ¼ck", callback_data=f"{chat_id}_{action.split('_')[0]}")
    ]])
    await msg.reply_text(f"âœ… {label} gesetzt.", reply_markup=kb)

async def topiclimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg  = update.effective_message
    args = context.args or []

    # auch ohne topic_id nutzbar, wenn im Thread ausgefÃ¼hrt
    tid = getattr(msg, "message_thread_id", None)
    if len(args) >= 2 and args[0].isdigit():
        tid = int(args[0])
        try:
            limit = int(args[1])
        except:
            return await msg.reply_text("Bitte eine Zahl fÃ¼r das Limit angeben.")
    elif tid is not None and len(args) >= 1:
        try:
            limit = int(args[0])
        except:
            return await msg.reply_text("Bitte eine Zahl fÃ¼r das Limit angeben.")
    else:
        return await msg.reply_text("Nutzung: /topiclimit <topic_id> <anzahl>\nOder im Ziel-Topic: /topiclimit <anzahl>")

    set_spam_policy_topic(chat.id, tid, per_user_daily_limit=max(0, limit))
    return await msg.reply_text(f"âœ… Limit fÃ¼r Topic {tid} gesetzt: {limit}/Tag/User (0 = aus).")

async def myquota_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    user = update.effective_user
    tid = getattr(msg, "message_thread_id", None)
    if tid is None:
        return await msg.reply_text("Bitte im gewÃ¼nschten Topic ausfÃ¼hren (Thread Ã¶ffnen) oder: /myquota <topic_id>")

    # Policy ermitteln (inkl. Topic-Override)
    link_settings = get_link_settings(chat.id)
    policy = effective_spam_policy(chat.id, tid, link_settings)
    daily_lim = int(policy.get("per_user_daily_limit") or 0)
    if daily_lim <= 0:
        return await msg.reply_text("FÃ¼r dieses Topic ist kein Tageslimit gesetzt.")

    used = count_topic_user_messages_today(chat.id, tid, user.id, tz="Europe/Berlin")
    remaining = max(daily_lim - used, 0)
    await msg.reply_text(f"Dein Restkontingent heute in diesem Topic: {remaining}/{daily_lim}")


async def mood_question_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if context.user_data.get('awaiting_mood_question'):
        grp = context.user_data.pop('mood_group_id')
        new_question = message.text
        set_mood_question(grp, new_question)
        context.user_data.pop('awaiting_mood_question', None)
        await message.reply_text(tr('âœ… Neue Mood-Frage gespeichert.', get_group_language(grp)))

async def nightmode_enforcer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    chat = update.effective_chat
    if not msg or chat.type not in ("group","supergroup"):
        return
    if getattr(msg, "new_chat_members", None) or getattr(msg, "left_chat_member", None):
        return
    if not getattr(msg, "from_user", None):
        return
    enabled, start_minute, end_minute, del_non, warn_once, tz_str, hard_mode, override_until = get_night_mode(chat.id)
    if not enabled:
        return

    tz = ZoneInfo(tz_str or "Europe/Berlin")
    now = datetime.datetime.now(tz)
    start_t = datetime.time(start_minute//60, start_minute%60)
    end_t   = datetime.time(end_minute//60, end_minute%60)
    active = (now.time() >= start_t or now.time() < end_t) if start_t > end_t else (start_t <= now.time() < end_t)
    if override_until:
        active = now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None) < override_until.replace(tzinfo=None)

    if not active:
        return
    if not del_non:
        return

    # Nur Nicht-Admins lÃ¶schen
    try:
        m = await context.bot.get_chat_member(chat.id, msg.from_user.id)
        if str(getattr(m, "status", "")).lower() in ("administrator","creator"):
            return
    except Exception:
        pass

    try:
        await msg.delete()
    except Exception:
        pass

async def set_topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg   = update.effective_message
    chat  = update.effective_chat
    user  = update.effective_user

    if not msg or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await update.message.reply_text("Nur in Gruppen nutzbar.")

    # Admin-Check (korrekte Helper-Funktion!)
    if not await _is_admin(context, chat.id, user.id):
        return await update.message.reply_text("Nur Admins dürfen das.")

    # Topic ermitteln (Thread)
    topic_id = getattr(msg, "message_thread_id", None)

    # Ziel-User suchen: 1) Reply  2) TEXT_MENTION  3) MENTION (@username)  4) Arg @username  5) Fallback: AusfÃ¼hrender im Topic
    target_user = None

    # 1) Reply bevorzugt
    if msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        target_user = msg.reply_to_message.from_user
        topic_id = topic_id or getattr(msg.reply_to_message, "message_thread_id", None)

    # 2) TEXT_MENTION (echter User in Entity)
    if not target_user and msg.entities:
        for ent in msg.entities:
            if ent.type == MessageEntity.TEXT_MENTION and getattr(ent, "user", None):
                target_user = ent.user
                break

    # 3) MENTION (@username) innerhalb der Nachricht
    if not target_user and msg.entities:
        for ent in msg.entities:
            if ent.type == MessageEntity.MENTION:
                uname = (msg.text or "")[ent.offset: ent.offset + ent.length]
                target_user = await _resolve_username_to_user(context, chat.id, uname)
                if target_user:
                    break

    # 4) @username als Argument
    if not target_user:
        args = context.args or []
        if args and args[0].startswith("@"):
            target_user = await _resolve_username_to_user(context, chat.id, args[0])

    # 5) Fallback: im Thread ohne Ziel â†’ den AusfÃ¼hrenden nehmen
    if not target_user and topic_id:
        target_user = user

    if not topic_id:
        return await update.message.reply_text("Bitte im gewünschten Topic ausführen oder auf eine Nachricht im Ziel-Topic antworten.")
    if not target_user:
        return await update.message.reply_text("Kein Nutzer erkannt. Antworte auf eine Nachricht oder nutze @username.")

    try:
        assign_topic(chat.id, target_user.id, topic_id, None)
        return await update.message.reply_text(
            f"âœ… Ausnahme gesetzt: {target_user.mention_html()} â†’ Topic {topic_id}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"/settopic failed: {e}", exc_info=True)
        return await update.message.reply_text("âŒ Konnte nicht speichern.")

    
async def remove_topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg    = update.effective_message
    chat   = update.effective_chat
    sender = update.effective_user

    # 0) Nur Admins dÃ¼rfen
    admins = await context.bot.get_chat_administrators(chat.id)
    if sender.id not in [admin.user.id for admin in admins]:
        return await msg.reply_text("âŒ Nur Admins dÃ¼rfen Themen entfernen.")
    
    # 1) Reply-Fallback (wenn per Reply getippt wird):
    target = None
    if msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        target = msg.reply_to_message.from_user

    # 2) Text-Mention aus MenÃ¼ (ent.user ist direkt verfÃ¼gbar):
    if not target and msg.entities:
        for ent in msg.entities:
            if ent.type == MessageEntity.TEXT_MENTION and getattr(ent, 'user', None):
                target = ent.user
                break
            # Inline-Link-Mention: tg://user?id=â€¦
            if ent.type == MessageEntity.TEXT_LINK and ent.url.startswith("tg://user?id="):
                uid = int(ent.url.split("tg://user?id=")[1])
                target = await context.bot.get_chat_member(chat.id, uid)
                target = target.user
                break

    # 3) @username-Mention (fÃ¼r alle, nicht nur Admins):
    if not target and context.args:
        text = context.args[0]
        name = text.lstrip('@')
        # suche in Chat-Admins und -Mitgliedern
        try:
            member = await context.bot.get_chat_member(chat.id, name)
            target = member.user
        except BadRequest:
            target = None

    # 4) Wenn immer noch kein Ziel â†’ Usage-Hinweis
    if not target:
        return await msg.reply_text(
            "âš ï¸ Ich konnte keinen User finden. Bitte antworte auf seine Nachricht "
            "oder nutze eine Mention (z.B. aus dem MenÃ¼)."
        )

    # 5) In DB lÃ¶schen und BestÃ¤tigung
    remove_topic(chat.id, target.id)
    display = f"@{target.username}" if target.username else target.first_name
    await msg.reply_text(f"ðŸš« {display} wurde als Themenbesitzer entfernt.")

async def faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg  = update.effective_message
    txt  = " ".join(context.args or []).strip()

    if not txt and msg and msg.reply_to_message:
        txt = (msg.reply_to_message.text or msg.reply_to_message.caption or "").strip()

    if not txt:
        return await msg.reply_text("Nutze: /faq <Stichwort oder Frage> oder antworte mit /faq auf eine Nachricht.")

    hit = find_faq_answer(chat.id, txt)
    if hit:
        _, ans = hit
        return await msg.reply_text(ans, parse_mode="HTML")
   # --- KI-Fallback (nur wenn aktiviert & Pro & Key vorhanden) ---
    from .database import get_ai_settings, is_pro_chat, log_auto_response, get_group_language
    from .utils import ai_available, ai_summarize
    ai_faq, _ = get_ai_settings(chat.id)
    if not ai_faq:
        logging.info("[FAQ_CMD] KI-Fallback aus (ai_faq_enabled=False)")
        return await msg.reply_text("Keine passende FAQ gefunden.")
    if not is_pro_chat(chat.id):
        logging.info("[FAQ_CMD] KI-Fallback gesperrt (kein Pro)")
        return await msg.reply_text("Keine passende FAQ gefunden.")
    if not ai_available():
        logging.info("[FAQ_CMD] KI-Fallback nicht verfÃ¼gbar (kein OPENAI_API_KEY)")
        return await msg.reply_text("Keine passende FAQ gefunden.")

    lang = get_group_language(chat.id) or "de"
    context_info = (
        "NÃ¼tzliche Infos: Website https://greeny187.github.io/GreenyManagementBots/ â€¢ "
        "Support: https://t.me/+DkUfIvjyej8zNGVi â€¢ "
        "Spenden: PayPal greeny187@outlook.de"
    )
    prompt = f"Frage: {txt}\n\n{context_info}\n\nAntworte knapp (2â€“3 SÃ¤tze) auf {lang}."
    try:
        answer = await ai_summarize(prompt, lang=lang)
        logging.info(f"[FAQ_CMD] KI-Fallback len={len(answer or '')}")
    except Exception as e:
        logging.exception(f"[FAQ_CMD] KI-Fallback Fehler: {e}")
        answer = None
    if answer:
        log_auto_response(chat.id, "AI/faq_cmd", 0.5, answer[:200], 0, None)
        return await msg.reply_text(answer, parse_mode="HTML")
    return await msg.reply_text("Keine passende FAQ gefunden.") 

async def show_rules_cmd(update, context):
    chat_id = update.effective_chat.id
    rec = get_rules(chat_id)
    if not rec:
        await update.message.reply_text("Keine Regeln gesetzt.")
    else:
        photo_id, text = rec
        if photo_id:
            await context.bot.send_photo(chat_id, photo_id, caption=text or "")
        else:
            await update.message.reply_text(text)

async def track_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    cm  = update.chat_member or update.my_chat_member

    # 1) Service-Messages (neue/gehende Mitglieder) per Message-Event
    if msg:
        chat_id = msg.chat.id

        # a) Neue Mitglieder (klassischer Service-Post)
        if msg.new_chat_members:
            for user in msg.new_chat_members:
                rec = get_welcome(chat_id)
                photo_id, text = (rec if rec else (None, "👋 Willkommen {user}!"))
                text = (text or "👋 Willkommen {user}!").replace(
                    "{user}", f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
                )
                try:
                    if photo_id:
                        await context.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML")
                    else:
                        await context.bot.send_message(chat_id, text=text, parse_mode="HTML")
                except Exception:
                    pass

                try:
                    add_member(chat_id, user.id)
                except Exception:
                    pass

                # Captcha (optional)
                enabled, ctype, behavior = get_captcha_settings(chat_id)
                if enabled:
                    if ctype == 'button':
                        kb = InlineKeyboardMarkup([[
                            InlineKeyboardButton("✅ Ich bin kein Bot", callback_data=f"{chat_id}_captcha_button_{user.id}")
                        ]])
                        sent = await context.bot.send_message(chat_id, f"🔐 Bitte bestätige, {user.first_name}.", reply_markup=kb)
                        context.bot_data[f"captcha:{chat_id}:{user.id}"] = {
                            "msg_id": sent.message_id, "behavior": behavior,
                            "issued_at": datetime.datetime.utcnow()
                        }
                    elif ctype == 'math':
                        a, b = random.randint(1,9), random.randint(1,9)
                        sent = await context.bot.send_message(
                            chat_id, f"🔐 Bitte rechne: {a} + {b} = ?", reply_markup=ForceReply(selective=True)
                        )
                        context.bot_data[f"captcha:{chat_id}:{user.id}"] = {
                            "answer": a+b, "behavior": behavior,
                            "issued_at": datetime.datetime.utcnow(), "msg_id": sent.message_id
                        }
            return

        # b) Verlassene Mitglieder
        if msg.left_chat_member:
            user = msg.left_chat_member
            rec = get_farewell(chat_id)
            photo_id, text = (rec if rec else (None, "👋 Auf Wiedersehen, {user}!"))
            text = (text or "👋 Auf Wiedersehen, {user}!").replace(
                "{user}", f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
            )
            try:
                if photo_id:
                    await context.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML")
                else:
                    await context.bot.send_message(chat_id, text=text, parse_mode="HTML")
            except Exception:
                pass
            try:
                remove_member(chat_id, user.id)
            except Exception:
                pass
            return

    # 2) ChatMember-Updates (Join/Leave ohne Service-Post / via Einladungslink)
    if cm:
        chat_id = cm.chat.id
        user    = cm.new_chat_member.user
        old_s   = cm.old_chat_member.status
        new_s   = cm.new_chat_member.status

        # Join
        if old_s in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED) and new_s in (
            ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER
        ):
            rec = get_welcome(chat_id)
            photo_id, text = (rec if rec else (None, "👋 Willkommen {user}!"))
            text = (text or "👋 Willkommen {user}!").replace(
                "{user}", f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
            )
            try:
                if photo_id:
                    await context.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML")
                else:
                    await context.bot.send_message(chat_id, text=text, parse_mode="HTML")
            except Exception:
                pass
            try:
                add_member(chat_id, user.id)
            except Exception:
                pass
            return

        # Leave
        if new_s in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
            rec = get_farewell(chat_id)
            photo_id, text = (rec if rec else (None, "👋 Auf Wiedersehen, {user}!"))
            text = (text or "👋 Auf Wiedersehen, {user}!").replace(
                "{user}", f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
            )
            try:
                if photo_id:
                    await context.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML")
                else:
                    await context.bot.send_message(chat_id, text=text, parse_mode="HTML")
            except Exception:
                pass
            try:
                remove_member(chat_id, user.id)
            except Exception:
                pass
            return
        
async def cleandelete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = [a.lower() for a in (context.args or [])]
    dry   = ("--dry-run" in args) or ("--dry" in args)
    demote = ("--demote" in args)

    count = await clean_delete_accounts_for_chat(chat_id, context.bot,
                                                 dry_run=dry, demote_admins=demote)
    prefix = "ðŸ”Ž Vorschau" if dry else "âœ… Entfernt"
    suffix = " (inkl. Admin-Demote)" if demote else ""
    await update.message.reply_text(f"{prefix}: {count} gelÃ¶schte Accounts{suffix}.")


async def spamlevel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg  = update.effective_message
    topic_id = getattr(msg, "message_thread_id", None)
    args = [a.lower() for a in (context.args or [])]

    # Anzeige (ohne Args)
    if not args:
        cur = get_spam_policy_topic(chat.id, topic_id or 0)
        return await msg.reply_text(
            "Nutze: /spamlevel off|light|medium|strict\n"
            "Optionale Flags:\n"
            " emoji=N  emoji_per_min=N  flood10s=N\n"
            " whitelist=dom1,dom2  blacklist=dom3,dom4\n"
            f"Aktuell (Topic {topic_id or 0}): {cur or 'keine Overrides'}"
        )

    level = args[0] if args[0] in ("off","light","medium","strict") else None
    fields = {}
    for a in args[1:]:
        if "=" in a:
            k,v = a.split("=",1)
            if k=="emoji": fields["emoji_max_per_msg"] = int(v)
            elif k in ("emoji_per_min","emojimin"): fields["emoji_max_per_min"] = int(v)
            elif k in ("flood10s","rate"): fields["max_msgs_per_10s"] = int(v)
            elif k=="whitelist": fields["whitelist"] = [d.strip().lower() for d in v.split(",") if d.strip()]
            elif k=="blacklist": fields["blacklist"] = [d.strip().lower() for d in v.split(",") if d.strip()]
    if level: fields["level"] = level
    set_spam_policy_topic(chat.id, topic_id or 0, **fields)
    await msg.reply_text(f"âœ… Spam-Policy gesetzt (Topic {topic_id or 0}).")

async def router_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg  = update.effective_message
    args = context.args or []

    if not args or args[0] == "list":
        rules = list_topic_router_rules(chat.id)
        if not rules:
            return await msg.reply_text("Keine Router-Regeln. Beispiel:\n/router add 12345 keywords=kaufen,verkaufen")
        lines = [f"#{rid} â†’ topic {tgt} | {'ON' if en else 'OFF'} | del={do} warn={wn} | kw={kws or []} dom={doms or []}"
                 for (rid,tgt,en,do,wn,kws,doms) in rules]
        return await msg.reply_text("Regeln:\n" + "\n".join(lines))

    sub = args[0]
    if sub == "add":
        # Format: /router add <topic_id> keywords=a,b  ODER  /router add <topic_id> domains=x.com,y.com
        if len(args) < 3 or not args[1].isdigit():
            return await msg.reply_text("Format:\n/router add <topic_id> keywords=a,b\n/router add <topic_id> domains=x.com,y.com")
        tgt = int(args[1]); kws=[]; doms=[]
        for a in args[2:]:
            if a.startswith("keywords="): kws = [x.strip() for x in a.split("=",1)[1].split(",") if x.strip()]
            if a.startswith("domains="):  doms = [x.strip().lower() for x in a.split("=",1)[1].split(",") if x.strip()]
        if not kws and not doms:
            return await msg.reply_text("Bitte keywords=â€¦ oder domains=â€¦ angeben.")
        rid = add_topic_router_rule(chat.id, tgt, kws or None, doms or None)
        return await msg.reply_text(f"âœ… Regel #{rid} â†’ Topic {tgt} angelegt.")

    if sub == "del" and len(args) >= 2 and args[1].isdigit():
        delete_topic_router_rule(chat.id, int(args[1]))
        return await msg.reply_text("ðŸ—‘ Regel gelÃ¶scht.")

    if sub == "toggle" and len(args) >= 3 and args[1].isdigit():
        toggle_topic_router_rule(chat.id, int(args[1]), args[2].lower() in ("on","true","1"))
        return await msg.reply_text("ðŸ” Regel umgeschaltet.")

    return await msg.reply_text("Unbekannter Router-Befehl.")

async def sync_admins_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dev = os.getenv("DEVELOPER_CHAT_ID")
    if str(update.effective_user.id) != dev:
        return await update.message.reply_text("âŒ Nur Entwickler darf das tun.")
    total = 0
    for chat_id, _ in get_registered_groups():
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            for adm in admins:
                add_member(chat_id, adm.user.id)
                total += 1
        except Exception as e:
            logger.error(f"Fehler bei Sync Admins fÃ¼r {chat_id}: {e}")
    await update.message.reply_text(f"âœ… {total} Admin-EintrÃ¤ge in der DB angelegt.")

# Callback-Handler fÃ¼r Button-Captcha
async def button_captcha_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id_str, _, _, user_id_str = query.data.split("_")
    chat_id, target_uid = int(chat_id_str), int(user_id_str)
    clicker = update.effective_user.id if update.effective_user else None

    if clicker != target_uid:
        await query.answer("âŒ Dieses Captcha ist nicht fÃ¼r dich.", show_alert=True)
        return

    key = f"captcha:{chat_id}:{target_uid}"
    data = context.bot_data.pop(key, None)
    
    # Captcha-Nachricht lÃ¶schen
    if data and data.get("msg_id"):
        try:
            await context.bot.delete_message(chat_id, data["msg_id"])
        except Exception:
            pass

    # NUR kurze BestÃ¤tigung, KEIN MenÃ¼
    await query.answer("âœ… Verifiziert! Willkommen in der Gruppe.", show_alert=False)

# Message-Handler fÃ¼r Mathe-Antworten
async def math_captcha_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    user_id = msg.from_user.id
    key = f"captcha:{chat_id}:{user_id}"
    data = context.bot_data.get(key)
    if not data:
        return

    # Timeout prÃ¼fen (60s)
    if (datetime.datetime.utcnow() - data['issued_at']).seconds > 60:
        # Fehlverhalten wie gehabt (kick oder stumm), nur Beispiel:
        try:
            beh = (data.get("behavior") or "").lower()
            if beh == "kick":
                await context.bot.ban_chat_member(chat_id, user_id)
                await context.bot.unban_chat_member(chat_id, user_id)
            elif beh in ("mute", "stumm"):
                await context.bot.restrict_chat_member(chat_id, user_id, ChatPermissions(can_send_messages=False))
        except Exception:
            pass
        # Captcha-Message wegrÃ¤umen
        mid = data.get("msg_id")
        if mid:
            try:
                await context.bot.delete_message(chat_id, mid)
            except Exception:
                pass
        context.bot_data.pop(key, None)
        return

    # Antwort prÃ¼fen
    try:
        if int((msg.text or "").strip()) == int(data.get("answer", -1)):
            # Erfolg: Captcha-Nachricht lÃ¶schen, keinen weiteren Text senden
            mid = data.get("msg_id")
            if mid:
                try:
                    await context.bot.delete_message(chat_id, mid)
                except Exception as e:
                    logger.debug(f"Captcha-Message delete failed ({chat_id}/{mid}): {e}")
            context.bot_data.pop(key, None)
            # Optional: Entmute aufheben, falls ihr beim Join einschrÃ¤nkt
            # await context.bot.restrict_chat_member(chat_id, user_id, ChatPermissions(can_send_messages=True))
        else:
            # Falsch: wie gehabt (kick/stumm) umsetzen
            try:
                beh = (data.get("behavior") or "").lower()
                if beh == "kick":
                    await context.bot.ban_chat_member(chat_id, user_id)
                    await context.bot.unban_chat_member(chat_id, user_id)
                elif beh in ("mute", "stumm"):
                    await context.bot.restrict_chat_member(chat_id, user_id, ChatPermissions(can_send_messages=False))
            except Exception:
                pass
            # Captcha-Message wegrÃ¤umen
            mid = data.get("msg_id")
            if mid:
                try:
                    await context.bot.delete_message(chat_id, mid)
                except Exception:
                    pass
            context.bot_data.pop(key, None)
    except ValueError:
        # UngÃ¼ltige Eingabe ignorieren
        pass

def register_handlers(app):
    app.add_handler(CommandHandler("start", start), group=-3)
    app.add_handler(CommandHandler("version", version), group=-3)
    app.add_handler(CommandHandler("rules", show_rules_cmd), group=-3)
    app.add_handler(CommandHandler("settopic", set_topic_cmd, filters=filters.ChatType.GROUPS), group=-3)
    app.add_handler(CommandHandler("router", router_command), group=-3)
    app.add_handler(CommandHandler("spamlevel", spamlevel_command), group=-3)
    app.add_handler(CommandHandler("topiclimit", topiclimit_command), group=-3)
    app.add_handler(CommandHandler("sync_admins_all", sync_admins_all, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("faq", faq_command), group=-3)
    app.add_handler(CommandHandler("myquota", myquota_command), group=-3)
    app.add_handler(CommandHandler("mystrikes", mystrikes_command), group=-3)
    app.add_handler(CommandHandler("strikes", strikes_command), group=-3)
    app.add_handler(CommandHandler("quietnow", quietnow_cmd, filters=filters.ChatType.GROUPS), group=-3)
    app.add_handler(CommandHandler("removetopic", remove_topic_cmd), group=-3)
    app.add_handler(CommandHandler("cleandeleteaccounts", cleandelete_command, filters=filters.ChatType.GROUPS), group=-3)

    # --- Callbacks / spezielle Replies ---
    # ggf. weitere CallbackQueryHandler hier

    # --- FrÃ¼he Message-Guards (keine Commands!) ---
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, forum_topic_registry_tracker), group=-2)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, nightmode_enforcer), group=-2)

    # --- Logging / leichte Helfer ---
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_logger), group=0)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, faq_autoresponder), group=-1)

    # --- Moderation ---
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, spam_enforcer), group=-3)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, ai_moderation_enforcer), group=-1)

    # --- Mitglieder-Events ---
    app.add_handler(ChatMemberHandler(_on_admin_change, ChatMemberHandler.CHAT_MEMBER), group=-4)
    app.add_handler(ChatMemberHandler(track_members, ChatMemberHandler.CHAT_MEMBER), group=-4)
    app.add_handler(ChatMemberHandler(track_members, ChatMemberHandler.MY_CHAT_MEMBER), group=-4)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, track_members), group=-4)
    app.add_handler(CallbackQueryHandler(button_captcha_handler, pattern=r"^-?\d+_captcha_button_\d+$"), group=-3)
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT, math_captcha_handler), group=-3)

    # (Optional) Fallback-Text-Handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler), group=3)

    # Hilfe (wenn du einen help_handler als Conversation/Handler-Objekt hast)
    app.add_handler(help_handler, group=5)
    app.add_error_handler(error_handler)

