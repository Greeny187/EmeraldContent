from telegram import ChatMemberAdministrator, ChatMemberOwner
from typing import Tuple
import time, asyncio

async def is_admin_or_owner(bot, chat_id: int, user_id: int) -> tuple[bool, bool]:
    try:
        cm = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return (False, False)
    is_owner = isinstance(cm, ChatMemberOwner) or getattr(cm, "status", "") == "creator"
    is_admin = is_owner or isinstance(cm, ChatMemberAdministrator) or getattr(cm, "status", "") == "administrator"
    return (is_admin, is_owner)

async def cached_admins(bot, context, chat_id: int, max_age=120):
    cache = context.bot_data.setdefault("admins_cache", {})
    entry = cache.get(chat_id)
    now = time.time()
    if entry and now - entry["ts"] < max_age:
        return entry["admins"]

    locks = context.bot_data.setdefault("admins_cache_locks", {})
    lock = locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        entry = cache.get(chat_id)
        if entry and now - entry["ts"] < max_age:
            return entry["admins"]
        admins = await bot.get_chat_administrators(chat_id)
        cache[chat_id] = {"ts": time.time(), "admins": admins}
        return admins

async def get_visible_groups(user_id: int, bot, all_groups):
    """
    Gibt nur Gruppen zurück, in denen der Bot aktiv ist und der Nutzer Admin/Owner ist.
    Nutzt get_chat_member (1 Call/Gruppe) statt komplette Adminliste.
    """
    visible = []
    for chat_id, title in all_groups:
        try:
            is_admin, is_owner = await is_admin_or_owner(bot, chat_id, user_id)
            if is_admin or is_owner:
                visible.append((chat_id, title))
        except Exception:
            continue
    return visible

async def resolve_privileged_flags(message, context) -> Tuple[bool, bool, bool, bool, int, int]:
    """
    Liefert (is_owner, is_admin, is_anon_admin, is_topic_owner, chat_id, user_id).

    - Owner/Admin werden via get_chat_member festgestellt
    - Anonyme Admins/Inhaber: message.sender_chat.id == chat.id
    - Topic-Owner: aktuell immer False (kann erweitert werden)
    """
    is_owner = False
    is_admin = False
    is_anon_admin = False
    is_topic_owner = False  # kann spÃ¤ter erweitert werden
    chat_id = message.chat.id
    user_id = None

    # 1) Anonyme Admins/Inhaber senden "im Namen der Gruppe"
    if getattr(message, "sender_chat", None) and message.sender_chat.id == chat_id:
        is_anon_admin = True
        return (is_owner, is_admin, is_anon_admin, is_topic_owner, chat_id, user_id)

    # 2) Normale User
    from_user = getattr(message, "from_user", None)
    if not from_user:
        return (is_owner, is_admin, is_anon_admin, is_topic_owner, chat_id, user_id)

    user_id = from_user.id

    # 3) ChatMember-Status prÃ¼fen
    try:
        cm = await context.bot.get_chat_member(chat_id, user_id)
        status = getattr(cm, "status", None)
        if status == "creator":
            is_owner = True
        elif status == "administrator":
            is_admin = True
    except Exception:
        pass  # bei Fehler lieber keine Privilegien annehmen

    return (is_owner, is_admin, is_anon_admin, is_topic_owner, chat_id, user_id)

