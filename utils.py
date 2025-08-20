import os
import re
import asyncio
import logging
from typing import Dict
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.ext import ExtBot
from telegram import ChatMember
from database import list_members, remove_member
from translator import translate_hybrid

logger = logging.getLogger(__name__)

# Heuristik: "Deleted Account" in verschiedenen Sprachen/Varianten
_DELETED_NAME_RX = re.compile(
    r"(deleted\s+account|gelösch(tes|ter)\s+(konto|account)|"
    r"(аккаунт\s+удалён|удалённый\s+аккаунт)|"
    r"(حساب\s+محذوف)|"
    r"(compte\s+supprimé)|"
    r"(cuenta\s+eliminada)|"
    r"(konto\s+gelöscht|konto\s+usunięte)|"
    r"(account\s+cancellato)|"
    r"(已删除的帐户|已刪除的帳號)|"
    r"(cont\s+șters)|"
    r"(счет\s+удален))",
    re.IGNORECASE
)

def _looks_deleted(user) -> bool:
    """Erkennt gelöschte Konten anhand Bot-API-Daten (Heuristik)."""
    if not user or getattr(user, "is_bot", False):
        return False
    name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
    # Typischerweise kein @username und Vorname wie "Deleted Account"
    if not getattr(user, "username", None) and _DELETED_NAME_RX.search(name or ""):
        return True
    return False

async def _get_member(bot: ExtBot, chat_id: int, user_id: int) -> ChatMember | None:
    try:
        return await bot.get_chat_member(chat_id, user_id)
    except RetryAfter as e:
        await asyncio.sleep(getattr(e, "retry_after", 1))
        return await bot.get_chat_member(chat_id, user_id)
    except BadRequest as e:
        # z. B. "Chat member not found" → nicht (mehr) Mitglied
        logger.debug(f"get_chat_member({chat_id},{user_id}) -> {e}")
        return None

async def clean_delete_accounts_for_chat(chat_id: int, bot: ExtBot, *, dry_run: bool = False) -> int:
    """
    Entfernt NUR gelöschte Accounts aus dem Chat (Ban+Unban) und räumt die DB-Mitgliederliste auf.
    Gibt die Anzahl tatsächlich entfernter (gekickter) gelöschter Accounts zurück.
    """
    user_ids = list_members(chat_id)  # aus eurer DB – keine Bot-API-Iteration nötig
    removed = 0

    for uid in user_ids:
        member = await _get_member(bot, chat_id, uid)
        if member is None:
            # Nicht (mehr) im Chat → nur DB aufräumen
            remove_member(chat_id, uid)
            continue

        # Admins/Owner NIE anfassen
        if member.status in ("administrator", "creator"):
            continue

        if _looks_deleted(member.user):
            if dry_run:
                removed += 1
                continue

            # Kick durch ban + optionales unban (so "verschwindet" der Ghost)
            try:
                await bot.ban_chat_member(chat_id, uid)
                try:
                    # Nur wenn gebannt – verhindert Fehlerflut
                    await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
                except BadRequest:
                    pass
                removed += 1
            except Forbidden as e:
                logger.warning(f"Keine Rechte um {uid} zu entfernen in {chat_id}: {e}")
            except BadRequest as e:
                logger.warning(f"Ban/Unban fehlgeschlagen für {uid} in {chat_id}: {e}")

            # Egal ob erfolgreich gebannt: aus eurer DB entfernen
            try:
                remove_member(chat_id, uid)
            except Exception as e:
                logger.debug(f"remove_member DB fail ({chat_id},{uid}): {e}")

    return removed

def tr(text: str, lang: str) -> str:
    try:
        return translate_hybrid(text, lang)
    except Exception as e:
        logger.error(f"Fehler in tr(): {e}")
        return text

def is_deleted_account(member) -> bool:
    """
    Erkenne gelöschte Accounts nur über Namensprüfung:
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

async def ai_summarize(text: str, lang: str = "de") -> str | None:
    """
    Sehr knapper TL;DR (1–2 Sätze) in 'lang'.
    - Opt-in per group_settings.ai_rss_summary
    - Falls OPENAI_API_KEY fehlt oder lib nicht installiert => None
    """
    key = os.getenv("OPENAI_API_KEY")
    if not key or not text:
        return None
    try:
        # lazy import (damit wir ohne openai laufen können)
        from openai import OpenAI
        client = OpenAI(api_key=key)
        prompt = (
            f"Fasse die folgende News extrem knapp auf {lang} zusammen "
            f"(max. 2 Sätze, keine Floskeln):\n\n{text}"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":"Du schreibst kurz, sachlich, deutsch."},
                      {"role":"user","content":prompt}],
            temperature=0.2,
            max_tokens=120,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.info(f"OpenAI unavailable: {e}")
        return None