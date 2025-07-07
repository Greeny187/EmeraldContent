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