import os
import re
import asyncio
import logging
import json
from urllib.parse import urlparse
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.ext import ExtBot
from telegram import ChatMember, ChatPermissions
from .database import list_members, remove_member
from shared.translator import translate_hybrid
from .ai_core import ai_available, ai_summarize, ai_moderate_image, ai_moderate_text

logger = logging.getLogger(__name__)

# Heuristik: "Deleted Account" in verschiedenen Sprachen/Varianten
_DELETED_NAME_RX = re.compile(
    r"(deleted\s+account|gelÃ¶sch(tes|ter)\s+(konto|account)|"
    r"(Ð°ÐºÐºÐ°ÑƒÐ½Ñ‚\s+ÑƒÐ´Ð°Ð»Ñ‘Ð½|ÑƒÐ´Ð°Ð»Ñ‘Ð½Ð½Ñ‹Ð¹\s+Ð°ÐºÐºÐ°ÑƒÐ½Ñ‚)|"
    r"(Ø­Ø³Ø§Ø¨\s+Ù…Ø­Ø°ÙˆÙ)|"
    r"(compte\s+supprimÃ©)|"
    r"(cuenta\s+eliminada)|"
    r"(konto\s+gelÃ¶scht|konto\s+usuniÄ™te)|"
    r"(account\s+cancellato)|"
    r"(å·²åˆ é™¤çš„å¸æˆ·|å·²åˆªé™¤çš„å¸³è™Ÿ)|"
    r"(cont\s+È™ters)|"
    r"(ÑÑ‡ÐµÑ‚\s+ÑƒÐ´Ð°Ð»ÐµÐ½))",
    re.IGNORECASE
)

def _looks_deleted(user) -> bool:
    """Erkennt gelÃ¶schte Konten anhand Bot-API-Daten (Heuristik)."""
    if not user or getattr(user, "is_bot", False):
        return False
    name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
    # Typischerweise kein @username und Vorname wie "Deleted Account"
    if not getattr(user, "username", None) and _DELETED_NAME_RX.search(name or ""):
        return True
    return False

async def _apply_hard_permissions(context, chat_id: int, active: bool):
    """
    Setzt fÃ¼r den Chat harte Schreibsperren (Nachtmodus) an/aus.
    active=True  -> can_send_messages=False
    active=False -> can_send_messages=True
    """
    try:
        if active:
            await context.bot.set_chat_permissions(
                chat_id=chat_id,
                permissions=ChatPermissions(can_send_messages=False)
            )
        else:
            await context.bot.set_chat_permissions(
                chat_id=chat_id,
                permissions=ChatPermissions(can_send_messages=True)
            )
    except Exception as e:
        logger.warning(f"Nachtmodus (hard) set_chat_permissions fehlgeschlagen: {e}")

async def _get_member(bot: ExtBot, chat_id: int, user_id: int) -> ChatMember | None:
    try:
        return await bot.get_chat_member(chat_id, user_id)
    except RetryAfter as e:
        await asyncio.sleep(getattr(e, "retry_after", 1))
        return await bot.get_chat_member(chat_id, user_id)
    except BadRequest as e:
        # z. B. "Chat member not found" â†’ nicht (mehr) Mitglied
        logger.debug(f"get_chat_member({chat_id},{user_id}) -> {e}")
        return None

async def clean_delete_accounts_for_chat(chat_id: int, bot: ExtBot, *,
                                         dry_run: bool = False,
                                         demote_admins: bool = False) -> int:
    """
    Entfernt gelÃ¶schte Accounts per ban+unban.
    - Entfernt DB-Eintrag NUR, wenn Kick erfolgreich war ODER der User nicht (mehr) im Chat ist.
    - Optional: demote_admins=True versucht gelÃ¶schte Admins zu demoten (erfordert Bot-Recht 'can_promote_members').
    """
    user_ids = list_members(chat_id)
    removed = 0

    for uid in user_ids:
        member = await _get_member(bot, chat_id, uid)
        if member is None:
            # Nicht (mehr) im Chat -> DB aufrÃ¤umen
            try: remove_member(chat_id, uid)
            except Exception: pass
            continue

        # GelÃ¶schten Status erkennen (robuster)
        looks_del = _looks_deleted(member.user) or is_deleted_account(member)

        # Admin/Owner-Handhabung
        if member.status in ("administrator", "creator"):
            if not looks_del or not demote_admins or member.status == "creator":
                # Creator (Owner) nie anfassen; Admins nur wenn demote_admins=True
                continue
            # Demote versuchen (alle Rechte false)
            try:
                await bot.promote_chat_member(
                    chat_id, uid,
                    can_manage_chat=False, can_post_messages=False, can_edit_messages=False,
                    can_delete_messages=False, can_manage_video_chats=False, can_invite_users=False,
                    can_restrict_members=False, can_pin_messages=False, can_promote_members=False,
                    is_anonymous=False
                )
                # Status neu laden
                member = await _get_member(bot, chat_id, uid)
            except Exception as e:
                logger.warning(f"Demote admin {uid} in {chat_id} fehlgeschlagen: {e}")
                continue  # ohne Demote kein Kick mÃ¶glich

        if not looks_del:
            continue

        if dry_run:
            removed += 1
            continue

        kicked = False
        try:
            await bot.ban_chat_member(chat_id, uid)
            try:
                await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
            except BadRequest:
                pass
            kicked = True
            removed += 1
        except Forbidden as e:
            logger.warning(f"Keine Rechte um {uid} zu entfernen in {chat_id}: {e}")
        except BadRequest as e:
            logger.warning(f"Ban/Unban fehlgeschlagen fÃ¼r {uid} in {chat_id}: {e}")

        # DB nur dann aufrÃ¤umen, wenn wirklich drauÃŸen
        try:
            member_after = await _get_member(bot, chat_id, uid)
            if kicked or member_after is None or getattr(member_after, "status", "") in ("left", "kicked"):
                remove_member(chat_id, uid)
        except Exception:
            pass

    return removed

def tr(text: str, lang: str) -> str:
    return translate_hybrid(text, target_lang=lang)

def is_deleted_account(member) -> bool:
    """
    Erkenne gelÃ¶schte Accounts nur Ã¼ber NamensprÃ¼fung:
    - Telegram ersetzt first_name durch 'Deleted Account'
    - oder entfernt alle Namen/Username
    """
    user = member.user
    first = (user.first_name or "").lower()
    # 1) Default-Titel 'Deleted Account' (manchmal abweichend 'Deleted account')
    if first.startswith("deleted account"):
        return True
    # 2) Kein Name, kein Username mehr vorhanden
    if not any([user.first_name, user.last_name, user.username]):
        return True
    return False

def _extract_domains_from_text(text:str) -> list[str]:
    if not text: return []
    urls = re.findall(r'(https?://\S+|www\.\S+)', text, flags=re.I)
    doms = []
    for u in urls:
        if not u.startswith("http"): u = "http://" + u
        try:
            d = urlparse(u).netloc.lower()
            if d.startswith("www."): d = d[4:]
            if d: doms.append(d)
        except: pass
    return doms

def heuristic_link_risk(domains:list[str]) -> float:
    """
    Grobe Risikobewertung fÃ¼r Links ohne AI: Shortener/Suspicious TLDs etc.
    """
    if not domains: return 0.0
    shorteners = {"bit.ly","tinyurl.com","goo.gl","t.co","ow.ly","buff.ly","shorturl.at","is.gd","rb.gy","cutt.ly"}
    bad_tlds   = {".ru",".cn",".tk",".gq",".ml",".ga",".cf"}
    score = 0.0
    for d in domains:
        if d in shorteners: score += 0.4
        if any(d.endswith(t) for t in bad_tlds): score += 0.3
        if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', d): score += 0.5  # blanke IP
    return min(1.0, score)

