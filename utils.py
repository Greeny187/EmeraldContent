import logging
from database import list_active_members, mark_member_deleted
from telegram.error import BadRequest
logger = logging.getLogger(__name__)

def is_deleted_account(member) -> bool:
    user = member.user
    status_deleted = member.status in ("left", "kicked")
    no_name        = not any([user.first_name, user.last_name, user.username])
    default_name   = (user.first_name or "").lower() == "deleted account"
    return status_deleted and (no_name or default_name)

async def clean_delete_accounts_for_chat(chat_id: int, bot) -> int:
    removed = []
    for user_id in list_active_members(chat_id):
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if is_deleted_account(member):
                await bot.ban_chat_member(chat_id, user_id)
                await bot.unban_chat_member(chat_id, user_id)
                mark_member_deleted(chat_id, user_id)
                removed.append(user_id)
                logger.debug(f"Markiert gelöscht: {user_id}")
        except BadRequest as e:
            logger.error(f"Cleanup-Error für {user_id}: {e.message}")
    logger.info(f"clean_delete: insgesamt {len(removed)} entfernt.")
    return len(removed)