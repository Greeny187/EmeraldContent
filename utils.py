import logging
from database import list_active_members, mark_member_deleted
from telegram.error import BadRequest
logger = logging.getLogger(__name__)

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

async def clean_delete_accounts_for_chat(chat_id: int, bot) -> int:
    removed = []
    for user_id in list_active_members(chat_id):
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if is_deleted_account(member):
                # Ban & Unban, um den Deleted-Account aus der Gruppe zu entfernen
                await bot.ban_chat_member(chat_id, user_id)
                await bot.unban_chat_member(chat_id, user_id)
                # Flag in der DB setzen
                mark_member_deleted(chat_id, user_id)
                removed.append(user_id)
                logger.debug(f"Markiert gelöscht: {user_id}")
        except BadRequest as e:
            logger.error(f"Cleanup-Error für {user_id}: {e.message}")
    logger.info(f"clean_delete: insgesamt {len(removed)} entfernt.")
    return len(removed)