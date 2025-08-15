import logging
import asyncio
import os
from database import list_active_members, mark_member_deleted
from telegram.error import BadRequest
from translator import translate_hybrid

logger = logging.getLogger(__name__)

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
    
async def clean_delete_accounts_for_chat(chat_id: int, bot) -> int:
    removed = []
    semaphore = asyncio.Semaphore(10)

    async def process(user_id):
        async with semaphore:
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                if is_deleted_account(member):
                    # Ban & Unban zum Entfernen
                    await bot.ban_chat_member(chat_id, user_id)
                    await bot.unban_chat_member(chat_id, user_id)
                    # Flag in DB setzen
                    mark_member_deleted(chat_id, user_id)
                    removed.append(user_id)
                    logger.debug(f"Markiert gelöscht: {user_id}")
            except BadRequest as e:
                logger.error(f"Cleanup-Error für {user_id}: {e.message}")

    # Alle IDs sammeln und parallel abarbeiten (max. 10 gleichzeitig)
    tasks = [process(uid) for uid in list_active_members(chat_id)]
    await asyncio.gather(*tasks)

    logger.info(f"clean_delete: insgesamt {len(removed)} entfernt.")
    return len(removed)