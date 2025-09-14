from telegram import ChatMemberAdministrator, ChatMemberOwner
from typing import Tuple
import time, asyncio

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

async def _get_chat_member_cached(context, chat_id: int, user_id: int, ttl=10):
    """
    Kleiner Cache für get_chat_member, damit ein Klick nicht 3–5 API-Calls erzeugt.
    context.bot_data wird dafür genutzt.
    """
    cache = context.bot_data.setdefault("cm_cache", {})
    key = (chat_id, user_id)
    now = time.time()
    entry = cache.get(key)
    if entry and (now - entry["ts"]) < ttl:
        return entry["cm"]
    cm = await context.bot.get_chat_member(chat_id, user_id)
    cache[key] = {"ts": now, "cm": cm}
    return cm

async def is_admin_or_owner(bot, chat_id: int, user_id: int, context=None) -> tuple[bool, bool]:
    """
    Liefert (is_admin, is_owner) – nutzt Cache, wenn context übergeben wird.
    """
    try:
        if context is not None:
            cm = await _get_chat_member_cached(context, chat_id, user_id)
        else:
            cm = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return (False, False)

    is_owner = isinstance(cm, ChatMemberOwner) or getattr(cm, "status", "") == "creator"
    is_admin = is_owner or isinstance(cm, ChatMemberAdministrator) or getattr(cm, "status", "") == "administrator"
    return (is_admin, is_owner)

async def get_visible_groups(user_id: int, bot, all_groups, context=None):
    """
    Nur Gruppen, in denen der Nutzer Admin/Owner ist – via get_chat_member statt Adminliste.
    """
    visible = []
    for chat_id, title in all_groups:
        try:
            if context is not None:
                cm = await _get_chat_member_cached(context, chat_id, user_id)
            else:
                cm = await bot.get_chat_member(chat_id, user_id)
            status = (getattr(cm, "status", "") or "").lower()
            if status in ("administrator", "creator"):
                visible.append((chat_id, title))
        except Exception:
            continue
    return visible

async def resolve_privileged_flags(message, context) -> Tuple[bool, bool, bool, bool, int, int]:
    """
    Liefert (is_owner, is_admin, is_anon_admin, is_topic_owner, chat_id, user_id).
    Nutzt ebenfalls den get_chat_member Cache.
    """
    is_owner = False
    is_admin = False
    is_anon_admin = False
    is_topic_owner = False
    chat_id = message.chat.id
    user_id = None

    if getattr(message, "sender_chat", None) and message.sender_chat.id == chat_id:
        is_anon_admin = True
        return (is_owner, is_admin, is_anon_admin, is_topic_owner, chat_id, user_id)

    from_user = getattr(message, "from_user", None)
    if not from_user:
        return (is_owner, is_admin, is_anon_admin, is_topic_owner, chat_id, user_id)

    user_id = from_user.id
    try:
        cm = await _get_chat_member_cached(context, chat_id, user_id)
        status = (getattr(cm, "status", "") or "").lower()
        if status == "creator":
            is_owner = True
        elif status == "administrator":
            is_admin = True
    except Exception:
        pass

    return (is_owner, is_admin, is_anon_admin, is_topic_owner, chat_id, user_id)

