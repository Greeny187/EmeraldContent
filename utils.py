import logging
from database import list_active_members, mark_member_deleted

logger = logging.getLogger(__name__)

async def clean_delete_accounts_for_chat(chat_id: int, bot) -> int:
    removed = []
    for user_id in list_active_members(chat_id):
        member = await bot.get_chat_member(chat_id, user_id)
        if is_deleted_account(member):
            await bot.ban_chat_member(chat_id, user_id)
            await bot.unban_chat_member(chat_id, user_id)
            mark_member_deleted(chat_id, user_id)
            removed.append(user_id)
    return len(removed)


def is_deleted_account(member) -> bool:
    """
    Prüft, ob es sich um einen Telegram ‘Deleted Account’ handelt.
    """
    return getattr(member.user, "is_deleted", False) or member.user.first_name == "Deleted Account"