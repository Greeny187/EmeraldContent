async def get_visible_groups(user_id: int, bot, all_groups):
    """
    Gibt nur Gruppen zurÃ¼ck, in denen der Bot aktiv ist und der Nutzer Admin ist.
    """
    visible = []
    for chat_id, title in all_groups:
        try:
            admins = await bot.get_chat_administrators(chat_id)
            if any(a.user.id == user_id for a in admins):
                visible.append((chat_id, title))
        except:
            continue
    return visible

from typing import Tuple

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
