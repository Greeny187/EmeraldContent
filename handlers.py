import os
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
from telegram.constants import ChatType
from database import (register_group, get_registered_groups, get_rules, set_welcome, set_rules, set_farewell, add_member, get_link_settings, 
remove_member, inc_message_count, assign_topic, remove_topic, has_topic, set_mood_question, get_farewell, get_welcome, get_captcha_settings,
get_night_mode, set_night_mode, get_group_language, get_link_settings, has_topic, set_spam_policy_topic, get_spam_policy_topic,
add_topic_router_rule, list_topic_router_rules, delete_topic_router_rule, get_effective_link_policy, is_pro_chat,
toggle_topic_router_rule, get_matching_router_rule, upsert_forum_topic, rename_forum_topic, find_faq_answer, log_auto_response, get_ai_settings,
effective_spam_policy, get_link_settings, has_topic, count_topic_user_messages_today, set_spam_policy_topic, 
effective_ai_mod_policy, log_ai_mod_action, count_ai_hits_today, set_ai_mod_settings, add_strike_points, get_strike_points, top_strike_users, decay_strikes
)
from zoneinfo import ZoneInfo
from patchnotes import __version__, PATCH_NOTES
from utils import (clean_delete_accounts_for_chat, ai_summarize, 
    ai_available, ai_moderate_text, ai_moderate_image, _extract_domains_from_text, 
    heuristic_link_risk, _apply_hard_permissions)
from user_manual import help_handler
from menu import show_group_menu, menu_free_text_handler
from statistic import log_spam_event, log_night_event
from access import get_visible_groups, resolve_privileged_flags
from translator import translate_hybrid

logger = logging.getLogger(__name__)

_EMOJI_RE = re.compile(r'([\U0001F300-\U0001FAFF\U00002600-\U000027BF])')
_URL_RE = re.compile(r'(https?://\S+|www\.\S+)', re.IGNORECASE)

def _extract_domains(text:str) -> list[str]:
    doms = []
    for m in _URL_RE.findall(text or ""):
        u = m if m.startswith("http") else f"http://{m}"
        try:
            dom = urlparse(u).netloc.lower()
            if dom.startswith("www."): dom = dom[4:]
            if dom: doms.append(dom)
        except Exception:
            pass
    return doms

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

async def _is_admin(bot, chat_id: int, user_id: int) -> bool:
    """True, wenn user_id in chat_id Admin/Owner ist."""
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return str(getattr(m, "status", "")).lower() in ("administrator", "creator")
    except Exception:
        return False

def _is_anon_admin_message(msg) -> bool:
    """True, wenn Nachricht als anonymer Admin (sender_chat == chat) kam."""
    try:
        return bool(getattr(msg, "sender_chat", None) and msg.sender_chat.id == msg.chat.id)
    except Exception:
        return False

async def _resolve_username_to_user(context, chat_id: int, username: str):
    """
    Versucht @username → telegram.User aufzulösen:
    1) Aus context.chat_data['username_map'] (gefüllt durch message_logger)
    2) Fallback: unter aktuellen Admins suchen
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

    # 2) Admin-Fallback
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        for a in admins:
            if a.user.username and a.user.username.lower() == name:
                return a.user
    except Exception:
        pass

    return None

def _is_quiet_now(start_min: int, end_min: int, now_min: int) -> bool:
    # Fenster über Mitternacht: start > end -> quiet wenn now >= start oder now < end
    if start_min == end_min:
        return False  # 0-Länge Fenster
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
    Löscht eine Nachricht robust:
    1) msg.delete()
    2) bot.delete_message(chat_id, message_id)
    Gibt True zurück, wenn gelöscht; sonst False (loggt Ursache).
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
    if not msg: return
    chat_id = msg.chat.id
    if _already_seen(context, chat_id, msg.message_id):
        return
    chat = update.effective_chat
    user = update.effective_user
    chat_id = chat.id
    text = msg.text or msg.caption or ""
    topic_id = getattr(msg, "message_thread_id", None)

    # Ausnahme: Admin / anonymer Admin / Topic-Owner

    is_owner, is_admin, is_anon_admin, is_topic_owner, chat_id, user_id = \
        await resolve_privileged_flags(msg, context)

    privileged = is_owner or is_admin or is_anon_admin or is_topic_owner
    if privileged:
        return  # Admins/Owner/Anonyme überspringen

    policy = get_effective_link_policy(chat_id, topic_id)
    domains_in_msg = _extract_domains(text)
    violation = False
    reason = None

    if domains_in_msg:
        # 1) Blacklist (Topic)
        for host in domains_in_msg:
            if any(host.endswith('.'+d) or host == d for d in (policy.get("blacklist") or [])):
                 violation = True; reason = "domain_blacklist"
                 break

        # 2) Nur-Admin-Links (global), Whitelist erlaubt
        if not violation and policy.get("admins_only") and not is_admin:
            def allowed(host):
                return any(host.endswith('.'+d) or host == d for d in (policy.get("whitelist") or []))
            if not any(allowed(h) for h in domains_in_msg):
                violation = True

    if violation:
        deleted = await _safe_delete(msg)
        # Aktion
        act = policy.get("action") or "delete"
        did = "delete" if deleted else "none"
        if act == "mute" and not is_admin:
            try:
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
                await context.bot.restrict_chat_member(
                    chat_id, msg.from_user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until
                )
                did += "/mute60m"
            except Exception as e:
                logger.warning(f"mute failed: {e}")

        # Hinweis nur einmal pro Nutzer/5s
        if _once(context, ("link_warn", chat_id, (msg.from_user.id if msg.from_user else 0)), ttl=5.0):
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=msg.message_thread_id,
                    text=policy.get("warning_text") or "🚫 Nur Admins dürfen Links posten."
                )
            except Exception:
                pass

        # Logging mit reason
        try:
            log_spam_event(chat_id, user.id if user else None, reason or "link_violation", did,
                           {"domains": domains_in_msg})
        except Exception:
            pass
        return
    
    # --- Tageslimit (pro Topic & User) --- 
    # separat die *Spam*-Policy laden (inkl. Topic-Overrides)
    link_flags = get_link_settings(chat_id)  # 4-Tuple aus DB
    spam_pol   = effective_spam_policy(chat_id, topic_id, link_flags)

    daily_lim   = int(spam_pol.get("per_user_daily_limit") or 0)
    notify_mode = (spam_pol.get("quota_notify") or "smart").lower()

    if topic_id and daily_lim > 0 and user and not privileged:
        # Zähle Nachrichten bis JETZT (vor dieser Nachricht)
        used_before = count_topic_user_messages_today(chat_id, topic_id, user.id, tz="Europe/Berlin")

        # Überschreitet diese Nachricht das Limit?
        if used_before >= daily_lim:
            deleted = await _hard_delete_message(context, chat_id, msg)

            did_action = "delete" if deleted else "none"
            primary = (spam_pol.get("action_primary") or "delete").lower()

            # Optional zusätzlich stumm schalten, wenn so konfiguriert
            if primary in ("mute", "stumm"):
                try:
                    until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
                    await context.bot.restrict_chat_member(
                        chat_id, user.id, ChatPermissions(can_send_messages=False), until_date=until
                    )
                    did_action = (did_action + "/mute60m") if did_action != "none" else "mute60m"
                except Exception as e:
                    logger.warning(f"Limit mute failed in {chat_id}: {e}")

            # Hinweis ins Topic (du wolltest beides: löschen + warnen)
            try:
                extra = " — Nutzer 60 Min. stumm." if "mute60m" in did_action else ""
                await context.bot.send_message(
                    chat_id=chat_id, message_thread_id=topic_id,
                    text=f"🚦 Limit erreicht: max. {daily_lim} Nachrichten/Tag in diesem Topic.{extra}",
                )
            except Exception:
                pass

            # Logging (best effort)
            try:
                from statistic import log_spam_event
                log_spam_event(
                    chat_id, user.id, "limit_day", did_action,
                    {"limit": daily_lim, "used_before": used_before, "topic_id": topic_id}
                )
            except Exception:
                pass

            return  # WICHTIG: nichts Weiteres mehr prüfen

        # Noch innerhalb des Limits: Rest nach dieser Nachricht anzeigen
        remaining_after = daily_lim - (used_before + 1)
        if notify_mode == "always" or (notify_mode == "smart" and (used_before in (0,) or remaining_after in (10, 5, 2, 1, 0))):
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    reply_to_message_id=msg.message_id,
                    text=f"🧮 Rest heute: {max(remaining_after,0)}/{daily_lim}"
                )
            except Exception:
                pass
    
    # 1) Topic-Router (nur wenn nicht bereits im Ziel-Topic)
    match = get_matching_router_rule(chat_id, text, domains_in_msg)
    if match and topic_id != match["target_topic_id"]:
        try:
            # Kopieren in Ziel-Topic
            await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=chat_id,
                message_id=msg.message_id,
                message_thread_id=match["target_topic_id"]
            )
            if match["delete_original"] and not privileged:
                await msg.delete()
            if match["warn_user"] and not privileged:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    text="↪️ Bitte ins passende Thema, habe deinen Beitrag verschoben."
                )
            # kein weiterer Spamcheck nötig – wir haben geroutet
            return
        except Exception as e:
            logger.warning(f"Router copy failed: {e}")

    # 2) Link-Blocking (nur Policy-basiert)
    if domains_in_msg and not privileged:
        wl = set(d.lower() for d in (policy.get("whitelist") or []))
        bl = set(d.lower() for d in (policy.get("blacklist") or []))
        if any(d in bl for d in domains_in_msg):
            try:
                await msg.delete()
                log_spam_event(chat_id, user.id if user else None, "link_blacklist", "delete",
                               {"domains": domains_in_msg})
            except Exception:
                pass
            return
        if policy.get("admins_only") and not is_admin:
            if not any((d in wl) for d in domains_in_msg):
                try:
                    await msg.delete()
                    log_spam_event(chat_id, user.id if user else None, "link_admins_only", "delete",
                                   {"domains": domains_in_msg})
                except Exception:
                    pass
                return

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
    if not text:  # (optional) Medien/OCR könntest du später ergänzen
        return

    topic_id = getattr(msg, "message_thread_id", None)
    # Pro-Gate: KI-Moderation nur in Pro-Gruppen
    if not is_pro_chat(chat.id):
        return
    policy = effective_ai_mod_policy(chat.id, topic_id)
    
    if not policy.get("enabled"):
        return

    # Privilegien
    admins = await context.bot.get_chat_administrators(chat.id)
    is_admin = any(a.user.id == (user.id if user else 0) for a in admins)
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

    # Primäraktion + Eskalation (heutige Treffer)
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

    warn_text = policy.get("warn_text") or "⚠️ Inhalt entfernt (KI-Moderation)."
    appeal_url = policy.get("appeal_url")

    try:
        # Delete (falls sinnvoll für alle Aktionsarten)
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
                    until = datetime.utcnow() + timedelta(minutes=int(policy.get("mute_minutes",60)))
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
        lines.append(f"• {uid}: {pts} Pkt")
    await update.effective_message.reply_text("Top-Strikes:\n" + "\n".join(lines))

async def faq_autoresponder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or chat.type not in ("group","supergroup") or not (msg.text or msg.caption):
        return
    text = msg.text or msg.caption

    # nur kurze Fragen / Hinweise triggern (heuristisch)
    if "?" not in text and not text.lower().startswith(("faq ", "/faq ")):
        logger.debug(f"[FAQ] skip: no trigger (text='{text[:60]}…')")
        return

    t0 = time.time()
    hit = find_faq_answer(chat.id, text)
    if hit:
        trig, ans = hit
        logger.debug(f"[FAQ] HIT via DB trigger='{trig}' latency_ms={(time.time()-t0)*1000:.0f}")
        await msg.reply_text(ans, parse_mode="HTML")
        dt = int((time.time()-t0)*1000)
        log_auto_response(chat.id, trig, 1.0, ans[:200], dt, None)
        return

    # optionaler KI-Fallback
    ai_faq, _ = get_ai_settings(chat.id)
    if not ai_faq or not is_pro_chat(chat.id):
        logger.debug("[FAQ] skip: AI fallback disabled in settings")
        return

    # sehr knapp, mit gruppenspezifischen Infos
    lang = get_group_language(chat.id) or "de"
    context_info = (
        "Nützliche Infos: Website https://greeny187.github.io/GreenyManagementBots/ • "
        "Support: https://t.me/+DkUfIvjyej8zNGVi • "
        "Spenden: PayPal greeny187@outlook.de"
    )
    prompt = f"Frage: {text}\n\n{context_info}\n\nAntworte knapp (2–3 Sätze) auf {lang}."

    try:
        # wir nutzen denselben Wrapper und 'missbrauchen' ai_summarize hier kurz
        answer = await ai_summarize(prompt, lang=lang)
        logger.debug(f"[FAQ] AI fallback called ok len={len(answer) if answer else 0}")
    except Exception:
        answer = None

    if answer:
        await msg.reply_text(answer, parse_mode="HTML")
        dt = int((time.time()-t0)*1000)
        log_auto_response(chat.id, "AI", 0.5, answer[:200], dt, None)

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
        await update.effective_message.reply_text(tr("✅ Startzeit gespeichert:", lang) + f" {txt}")
    else:
        set_night_mode(cid, end_minute=val)
        await update.effective_message.reply_text(tr("✅ Endzeit gespeichert:", lang) + f" {txt}")
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
            return await update.message.reply_text(tr("Nur Admins dürfen die Ruhephase starten.", lang))
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
    await update.message.reply_text(tr("🌙 Sofortige Ruhephase aktiv bis", lang) + f" {human} ({tz}).")

async def error_handler(update, context):
    """Fängt alle nicht abgefangenen Errors auf, loggt und benachrichtigt Telegram-Dev-Chat."""
    logger.error("Uncaught exception", exc_info=context.error)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type in ("group", "supergroup"):
        register_group(chat.id, chat.title)
        return await update.message.reply_text(
            "👋 Willkommen bei *Greeny Group Manager*!\n\n"
            "Ich helfe dir, deine Telegram-Gruppe automatisch zu verwalten – "
            "inklusive Schutz, Statistiken, Rollenverwaltung, Captcha u.v.m.\n\n"
            "🌐 Mehr Infos: [Zur Website](https://greeny187.github.io/GreenyManagementBots/)\n\n"
            "💚 *Unterstütze das Projekt:*\n"
            "• TON Wallet: `UQBopac1WFJGC_K48T8JqcbRoH3evUoUDwS2oItlS-SgpR8L`\n"
            "• PayPal: greeny187@outlook.de\n\n"
            "ℹ️ Tippe /help für alle Funktionen.\n\n"
            "✅ Gruppe registriert! Geh privat auf /menu.")

    if chat.type == "private":
        all_groups = get_registered_groups()
        visible_groups = await get_visible_groups(user.id, context.bot, all_groups)

        if not visible_groups:
            return await update.message.reply_text(
                "🚫 Du bist in keiner Gruppe Admin, in der der Bot aktiv ist.\n"
                "➕ Füge den Bot in eine Gruppe ein und gib ihm Adminrechte."
            )

        keyboard = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid, title in visible_groups]
        markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🔧 Wähle eine Gruppe:", reply_markup=markup)

async def menu_command(update, context):
    from database import get_registered_groups
    from access import get_visible_groups

    user = update.effective_user
    all_groups = get_registered_groups()
    visible_groups = await get_visible_groups(user.id, context.bot, all_groups)

    if not visible_groups:
        return await update.message.reply_text(
            "🚫 Du bist in keiner Gruppe Admin, in der der Bot aktiv ist.\n"
            "➕ Füge den Bot in eine Gruppe ein und gib ihm Adminrechte."
        )

    # Wenn nur eine Gruppe → direkt Menü zeigen
    if len(visible_groups) == 1:
        chat_id = visible_groups[0][0]
        context.user_data["selected_chat_id"] = chat_id
        return await show_group_menu(query=None, cid=chat_id, context=context, dest_chat_id=update.effective_chat.id)

    # Mehrere Gruppen → Auswahl anzeigen
    keyboard = [[InlineKeyboardButton(title, callback_data=f"group_{cid}")] for cid, title in visible_groups]
    await update.message.reply_text("🔧 Wähle eine Gruppe:", reply_markup=InlineKeyboardMarkup(keyboard))



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
    logger.info(f"💬 message_logger aufgerufen in Chat {update.effective_chat.id}")
    msg = update.effective_message
    if msg.chat.type in ("group", "supergroup") and msg.from_user:
        inc_message_count(msg.chat.id, msg.from_user.id, date.today())
        # neu: stelle sicher, dass jeder Schreiber in die members-Tabelle kommt
        try:
            add_member(msg.chat.id, msg.from_user.id)
            logger.info(f"➕ add_member via message_logger: chat={msg.chat.id}, user={msg.from_user.id}")
        except Exception as e:
            logger.info(f"Fehler add_member in message_logger: {e}", exc_info=True)

        # 🔹 NEU: Username→ID Map im Chat pflegen (für @username-Auflösung)
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
    - Niemals in Kanälen laufen
    - Mood-Frage beantworten
    - Bei irgendeinem offenen Menü-Flow (awaiting_*) an menu_free_text_handler delegieren
    - Sonst: nichts tun
    """
    msg   = update.effective_message
    chat  = update.effective_chat
    ud    = context.user_data or {}

    # Nur Privat/Gruppe/Supergruppe – Kanäle explizit ausschließen
    if chat.type not in (ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP):
        return

    # 1) Expliziter Mini-Flow: Mood-Frage
    if ud.get("awaiting_mood_question"):
        return await mood_question_handler(update, context)

    # 2) Zentrale Menü-Reply-Fallbacks
    #    Wenn irgendein Awaiting-Flag (oder last_edit) gesetzt ist,
    #    gib die Nachricht an den zentralen menu_free_text_handler ab.
    if any(k.startswith("awaiting_") for k in ud.keys()) or ("last_edit" in ud):
        return await menu_free_text_handler(update, context)

    # 3) Sonst: nichts – andere Aufgaben haben eigene Handler
    return

async def edit_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Nur aktiv, wenn zuvor im Menü „Bearbeiten“ gedrückt wurde
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
        label = "Begrüßung"
    elif action == "rules_edit":
        set_rules(chat_id, photo_id, text)
        label = "Regeln"
    elif action == "farewell_edit":
        set_farewell(chat_id, photo_id, text)
        label = "Farewell-Nachricht"
    else:
        return

    # Bestätigung mit Zurück-Button ins Menü
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅ Zurück", callback_data=f"{chat_id}_{action.split('_')[0]}")
    ]])
    await msg.reply_text(f"✅ {label} gesetzt.", reply_markup=kb)

async def topiclimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg  = update.effective_message
    args = context.args or []

    # auch ohne topic_id nutzbar, wenn im Thread ausgeführt
    tid = getattr(msg, "message_thread_id", None)
    if len(args) >= 2 and args[0].isdigit():
        tid = int(args[0])
        try:
            limit = int(args[1])
        except:
            return await msg.reply_text("Bitte eine Zahl für das Limit angeben.")
    elif tid is not None and len(args) >= 1:
        try:
            limit = int(args[0])
        except:
            return await msg.reply_text("Bitte eine Zahl für das Limit angeben.")
    else:
        return await msg.reply_text("Nutzung: /topiclimit <topic_id> <anzahl>\nOder im Ziel-Topic: /topiclimit <anzahl>")

    set_spam_policy_topic(chat.id, tid, per_user_daily_limit=max(0, limit))
    return await msg.reply_text(f"✅ Limit für Topic {tid} gesetzt: {limit}/Tag/User (0 = aus).")

async def myquota_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    user = update.effective_user
    tid = getattr(msg, "message_thread_id", None)
    if tid is None:
        return await msg.reply_text("Bitte im gewünschten Topic ausführen (Thread öffnen) oder: /myquota <topic_id>")

    # Policy ermitteln (inkl. Topic-Override)
    link_settings = get_link_settings(chat.id)
    policy = effective_spam_policy(chat.id, tid, link_settings)
    daily_lim = int(policy.get("per_user_daily_limit") or 0)
    if daily_lim <= 0:
        return await msg.reply_text("Für dieses Topic ist kein Tageslimit gesetzt.")

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
        await message.reply_text(tr('✅ Neue Mood-Frage gespeichert.', get_group_language(grp)))

async def nightmode_enforcer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    lang = get_group_language(chat.id) or 'de'
    user = update.effective_user
    if not msg or not chat or chat.type not in ("group","supergroup") or not user:
        return

    en, s, e, del_non_admin, warn_once, tz, hard_mode, override_until = get_night_mode(chat.id)
    now_local = datetime.datetime.now(ZoneInfo(tz))
    now_min = now_local.hour*60 + now_local.minute

    quiet_scheduled = en and _is_quiet_now(s, e, now_min)
    quiet_override  = bool(override_until and now_local < override_until)
    is_quiet = quiet_scheduled or quiet_override

    # Admins ausnehmen
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except Exception:
        admins = []
    is_admin = any(a.user.id == user.id for a in admins)
    is_anon_admin = bool(getattr(msg, 'sender_chat', None) and msg.sender_chat.id == chat.id)
    if is_admin or is_anon_admin:
        # Falls harter Modus aktiv ist, Admins sind eh ausgenommen
        return

    # Status-Flag im ChatContext (um set_chat_permissions nicht zu spammen)
    flags = context.chat_data.setdefault("nm_flags", {"hard_applied": False})

    if is_quiet:
        if hard_mode:
            if not flags["hard_applied"]:
                await _apply_hard_permissions(context, chat.id, True)
                flags["hard_applied"] = True
            # Nichts weiter tun – Permissions kümmern sich um die Sperre
            return
        else:
            # Weicher Modus: löschen und optional warnen
            try:
                if del_non_admin:
                    await msg.delete()
                if warn_once:
                    key = (now_local.date().isoformat(), user.id)
                    warned = context.chat_data.setdefault("nm_warned", set())
                    if key not in warned:
                        warned.add(key)
                        await context.bot.send_message(chat.id, tr("🌙 Ruhezeit aktiv – bitte poste wieder nach Ende der Nachtphase.", lang))
            except Exception as e:
                logger.warning(f"Nachtmodus (soft) Eingriff fehlgeschlagen: {e}")
            return
    else:
        # Ruhe vorbei: ggf. harte Sperre aufheben
        if hard_mode and flags.get("hard_applied"):
            await _apply_hard_permissions(context, chat.id, False)
            flags["hard_applied"] = False
        # Abgelaufene Overrides aufräumen (optional)
        if override_until and now_local >= override_until:
            set_night_mode(chat.id, override_until=None)

async def set_topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg   = update.effective_message
    chat  = update.effective_chat
    user  = update.effective_user

    if not msg or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await update.message.reply_text("Nur in Gruppen nutzbar.")

    # Admin-Check (korrekte Helper-Funktion!)
    if not await _is_admin(context.bot, chat.id, user.id):
        return await update.message.reply_text("Nur Admins dürfen das.")

    # Topic ermitteln (Thread)
    topic_id = getattr(msg, "message_thread_id", None)

    # Ziel-User suchen: 1) Reply  2) TEXT_MENTION  3) MENTION (@username)  4) Arg @username  5) Fallback: Ausführender im Topic
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

    # 5) Fallback: im Thread ohne Ziel → den Ausführenden nehmen
    if not target_user and topic_id:
        target_user = user

    if not topic_id:
        return await update.message.reply_text("Bitte im gewünschten Topic ausführen oder auf eine Nachricht im Ziel-Topic antworten.")
    if not target_user:
        return await update.message.reply_text("Kein Nutzer erkannt. Antworte auf eine Nachricht oder nutze @username.")

    try:
        assign_topic(chat.id, target_user.id, topic_id, None)
        return await update.message.reply_text(
            f"✅ Ausnahme gesetzt: {target_user.mention_html()} → Topic {topic_id}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"/settopic failed: {e}", exc_info=True)
        return await update.message.reply_text("❌ Konnte nicht speichern.")

    
async def remove_topic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg    = update.effective_message
    chat   = update.effective_chat
    sender = update.effective_user

    # 0) Nur Admins dürfen
    admins = await context.bot.get_chat_administrators(chat.id)
    if sender.id not in [admin.user.id for admin in admins]:
        return await msg.reply_text("❌ Nur Admins dürfen Themen entfernen.")
    
    # 1) Reply-Fallback (wenn per Reply getippt wird):
    target = None
    if msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        target = msg.reply_to_message.from_user

    # 2) Text-Mention aus Menü (ent.user ist direkt verfügbar):
    if not target and msg.entities:
        for ent in msg.entities:
            if ent.type == MessageEntity.TEXT_MENTION and getattr(ent, 'user', None):
                target = ent.user
                break
            # Inline-Link-Mention: tg://user?id=…
            if ent.type == MessageEntity.TEXT_LINK and ent.url.startswith("tg://user?id="):
                uid = int(ent.url.split("tg://user?id=")[1])
                target = await context.bot.get_chat_member(chat.id, uid)
                target = target.user
                break

    # 3) @username-Mention (für alle, nicht nur Admins):
    if not target and context.args:
        text = context.args[0]
        name = text.lstrip('@')
        # suche in Chat-Admins und -Mitgliedern
        try:
            member = await context.bot.get_chat_member(chat.id, name)
            target = member.user
        except BadRequest:
            target = None

    # 4) Wenn immer noch kein Ziel → Usage-Hinweis
    if not target:
        return await msg.reply_text(
            "⚠️ Ich konnte keinen User finden. Bitte antworte auf seine Nachricht "
            "oder nutze eine Mention (z.B. aus dem Menü)."
        )

    # 5) In DB löschen und Bestätigung
    remove_topic(chat.id, target.id)
    display = f"@{target.username}" if target.username else target.first_name
    await msg.reply_text(f"🚫 {display} wurde als Themenbesitzer entfernt.")

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
    # 0) Service-Messages behandeln: new_chat_members / left_chat_member
    msg = update.message
    chat_id = update.effective_chat.id

    if msg:
        chat_id = msg.chat.id
        # a) Neue Mitglieder
        if msg.new_chat_members:
            for user in msg.new_chat_members:
                # Willkommen wie unten
                rec = get_welcome(chat_id)
                if rec:
                    photo_id, text = rec
                    text = (text or "").replace(
                        "{user}", f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
                    )
                    if photo_id:
                        await context.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML")
                    else:
                        await context.bot.send_message(chat_id, text=text, parse_mode="HTML")
                add_member(chat_id, user.id)

                # 2) Captcha zusätzlich anzeigen, falls aktiviert
                enabled, ctype, behavior = get_captcha_settings(chat_id)
                if enabled:
                    if ctype == 'button':
                        kb = InlineKeyboardMarkup([[
                            InlineKeyboardButton("✅ Ich bin kein Bot", callback_data=f"{chat_id}_captcha_button_{user.id}")
                        ]])
                        sent = await context.bot.send_message(
                            chat_id,
                            text=f"🔐 Bitte bestätige, dass du kein Bot bist, {user.first_name}.",
                            reply_markup=kb
                        )
                        # Captcha-Message speichern (nur löschen bei Erfolg)
                        context.bot_data[f"captcha:{chat_id}:{user.id}"] = {
                            "msg_id": sent.message_id,
                            "behavior": behavior,
                            "issued_at": datetime.datetime.utcnow()
                        }
                    elif ctype == 'math':
                        a, b = random.randint(1,9), random.randint(1,9)
                        sent = await context.bot.send_message(
                            chat_id,
                            text=f"🔐 Bitte rechne: {a} + {b} = ?",
                            reply_markup=ForceReply(selective=True)
                        )
                        # In bot_data statt user_data speichern
                        context.bot_data[f"captcha:{chat_id}:{user.id}"] = {
                            "answer": a + b,
                            "behavior": behavior,
                            "issued_at": datetime.datetime.utcnow(),
                            "msg_id": sent.message_id
                        }
            return
        # b) Verlassene Mitglieder
        if msg.left_chat_member:
            user = msg.left_chat_member
            rec = get_farewell(chat_id)
            if rec:
                photo_id, text = rec
                text = (text or "").replace(
                    "{user}", 
                    f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
                )
                if photo_id:
                    await context.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML")
                else:
                    await context.bot.send_message(chat_id, text=text, parse_mode="HTML")
            remove_member(chat_id, user.id)
            return

    # 1) Willkommen verschicken
    if status in ("member", "administrator", "creator"):
        rec = get_welcome(chat_id)
        if rec:
            photo_id, text = rec
            # Nutzer direkt ansprechen:
            text = (text or "").replace("{user}", f"<a href='tg://user?id={user.id}'>{user.first_name}</a>")
            if photo_id:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_id,
                    caption=text,
                    parse_mode="HTML"
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML"
                )
  
        try:
            add_member(chat_id, user.id)
            logger.info(f"✅ add_member in DB: chat={chat_id}, user={user.id}")
        except Exception as e:
            logger.error(f"❌ add_member fehlgeschlagen: {e}", exc_info=True)
        return

    # 2) Abschied verschicken
    if status in ("left", "kicked"):
        rec = get_farewell(chat_id)
        if rec:
            photo_id, text = rec
            text = (text or "").replace("{user}", f"<a href='tg://user?id={user.id}'>{user.first_name}</a>")
            if photo_id:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_id,
                    caption=text,
                    parse_mode="HTML"
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML"
                )
        remove_member(chat_id, user.id)
        return
    
    cm = update.chat_member or update.my_chat_member
    if not cm:
        return
    chat_id = cm.chat.id
    user = cm.new_chat_member.user
    status = cm.new_chat_member.status
    logger.info(f"🔔 track_members aufgerufen: chat_id={update.effective_chat and update.effective_chat.id}, user={cm.new_chat_member.user.id}, status={cm.new_chat_member.status}")

async def cleandelete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = [a.lower() for a in (context.args or [])]
    dry   = ("--dry-run" in args) or ("--dry" in args)
    demote = ("--demote" in args)

    count = await clean_delete_accounts_for_chat(chat_id, context.bot,
                                                 dry_run=dry, demote_admins=demote)
    prefix = "🔎 Vorschau" if dry else "✅ Entfernt"
    suffix = " (inkl. Admin-Demote)" if demote else ""
    await update.message.reply_text(f"{prefix}: {count} gelöschte Accounts{suffix}.")


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
            elif k=="whitelist": fields["link_whitelist"] = [d.strip().lower() for d in v.split(",") if d.strip()]
            elif k=="blacklist": fields["domain_blacklist"] = [d.strip().lower() for d in v.split(",") if d.strip()]
    if level: fields["level"] = level
    set_spam_policy_topic(chat.id, topic_id or 0, **fields)
    await msg.reply_text(f"✅ Spam-Policy gesetzt (Topic {topic_id or 0}).")

async def router_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg  = update.effective_message
    args = context.args or []

    if not args or args[0] == "list":
        rules = list_topic_router_rules(chat.id)
        if not rules:
            return await msg.reply_text("Keine Router-Regeln. Beispiel:\n/router add 12345 keywords=kaufen,verkaufen")
        lines = [f"#{rid} → topic {tgt} | {'ON' if en else 'OFF'} | del={do} warn={wn} | kw={kws or []} dom={doms or []}"
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
            return await msg.reply_text("Bitte keywords=… oder domains=… angeben.")
        rid = add_topic_router_rule(chat.id, tgt, kws or None, doms or None)
        return await msg.reply_text(f"✅ Regel #{rid} → Topic {tgt} angelegt.")

    if sub == "del" and len(args) >= 2 and args[1].isdigit():
        delete_topic_router_rule(chat.id, int(args[1]))
        return await msg.reply_text("🗑 Regel gelöscht.")

    if sub == "toggle" and len(args) >= 3 and args[1].isdigit():
        toggle_topic_router_rule(chat.id, int(args[1]), args[2].lower() in ("on","true","1"))
        return await msg.reply_text("🔁 Regel umgeschaltet.")

    return await msg.reply_text("Unbekannter Router-Befehl.")

async def sync_admins_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dev = os.getenv("DEVELOPER_CHAT_ID")
    if str(update.effective_user.id) != dev:
        return await update.message.reply_text("❌ Nur Entwickler darf das tun.")
    total = 0
    for chat_id, _ in get_registered_groups():
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            for adm in admins:
                add_member(chat_id, adm.user.id)
                total += 1
        except Exception as e:
            logger.error(f"Fehler bei Sync Admins für {chat_id}: {e}")
    await update.message.reply_text(f"✅ {total} Admin-Einträge in der DB angelegt.")

# Callback-Handler für Button-Captcha
async def button_captcha_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id_str, _, _, user_id_str = query.data.split("_")
    chat_id, target_uid = int(chat_id_str), int(user_id_str)
    clicker = update.effective_user.id if update.effective_user else None

    if clicker != target_uid:
        await query.answer("❌ Dieses Captcha ist nicht für dich.", show_alert=True)
        return

    key = f"captcha:{chat_id}:{target_uid}"
    data = context.bot_data.pop(key, None)
    
    # Captcha-Nachricht löschen
    if data and data.get("msg_id"):
        try:
            await context.bot.delete_message(chat_id, data["msg_id"])
        except Exception:
            pass

    # NUR kurze Bestätigung, KEIN Menü
    await query.answer("✅ Verifiziert! Willkommen in der Gruppe.", show_alert=False)

# Message-Handler für Mathe-Antworten
async def math_captcha_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    user_id = msg.from_user.id
    key = f"captcha:{chat_id}:{user_id}"
    data = context.bot_data.get(key)
    if not data:
        return

    # Timeout prüfen (60s)
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
        # Captcha-Message wegräumen
        mid = data.get("msg_id")
        if mid:
            try:
                await context.bot.delete_message(chat_id, mid)
            except Exception:
                pass
        context.bot_data.pop(key, None)
        return

    # Antwort prüfen
    try:
        if int((msg.text or "").strip()) == int(data.get("answer", -1)):
            # Erfolg: Captcha-Nachricht löschen, keinen weiteren Text senden
            mid = data.get("msg_id")
            if mid:
                try:
                    await context.bot.delete_message(chat_id, mid)
                except Exception as e:
                    logger.debug(f"Captcha-Message delete failed ({chat_id}/{mid}): {e}")
            context.bot_data.pop(key, None)
            # Optional: Entmute aufheben, falls ihr beim Join einschränkt
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
            # Captcha-Message wegräumen
            mid = data.get("msg_id")
            if mid:
                try:
                    await context.bot.delete_message(chat_id, mid)
                except Exception:
                    pass
            context.bot_data.pop(key, None)
    except ValueError:
        # Ungültige Eingabe ignorieren
        pass

def register_handlers(app):
    app.add_handler(CommandHandler("start", start), group=-3)
    app.add_handler(CommandHandler("menu", menu_command), group=-3)
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

    # --- Frühe Message-Guards (keine Commands!) ---
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, forum_topic_registry_tracker), group=-1)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, nightmode_enforcer), group=-1)

    # --- Logging / leichte Helfer ---
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_logger), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, faq_autoresponder), group=0)

    # --- Moderation ---
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, spam_enforcer), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_moderation_enforcer), group=1)

    # --- Mitglieder-Events ---
    app.add_handler(ChatMemberHandler(track_members, ChatMemberHandler.CHAT_MEMBER), group=0)
    app.add_handler(ChatMemberHandler(track_members, ChatMemberHandler.MY_CHAT_MEMBER), group=0)

    # (Optional) Fallback-Text-Handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler), group=3)

    # Hilfe (wenn du einen help_handler als Conversation/Handler-Objekt hast)
    app.add_handler(help_handler, group=0)