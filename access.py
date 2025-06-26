async def get_visible_groups(user_id: int, bot, all_groups):
    """
    Gibt nur Gruppen zurück, in denen der Bot aktiv ist und der Nutzer Admin ist.
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

async def get_visible_channels(user_id: int, bot, all_channels):
    """
    Gibt nur diejenigen Kanäle zurück, in denen der Bot aktiv ist
    und der Nutzer Admin oder Inhaber ist.
    `all_channels` ist eine Liste von Tupeln (parent_chat_id, channel_id, title).
    """
    visible = []
    for parent_chat_id, channel_id, channel_username, channel_title in all_channels:
        try:
            # prüfe Rechte im Kanal selbst
            admins = await bot.get_chat_administrators(channel_id)
            if any(a.user.id == user_id for a in admins):
                visible.append((channel_id, channel_title))
        except:
            # überspringe Kanäle, in die der Bot (z. B.) nicht mehr eingeladen ist
            continue
    return visible