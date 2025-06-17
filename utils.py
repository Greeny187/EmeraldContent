from telegram.ext import ContextTypes
import logging
from database import list_members

logger = logging.getLogger(__name__)

async def clean_delete_accounts_for_chat(chat_id: int, bot) -> int:
    """
    Entfernt alle gelöschten Accounts in der DB-Liste per Ban+Unban
    und gibt die Anzahl der entfernten User zurück.
    """
    removed = []
    for user_id in list_members(chat_id):
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if getattr(member.user, "is_deleted", False) or member.user.first_name == "Deleted Account":
                await bot.ban_chat_member(chat_id, user_id)
                await bot.unban_chat_member(chat_id, user_id)
                removed.append(user_id)
        except Exception as e:
            logger.error(f"Error cleaning user {user_id} in chat {chat_id}: {e}")
    return len(removed)


def is_deleted_account(member) -> bool:
    """
    Prüft, ob es sich um einen Telegram ‘Deleted Account’ handelt.
    """
    return getattr(member.user, "is_deleted", False) or member.user.first_name == "Deleted Account"