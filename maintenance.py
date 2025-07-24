import asyncio
from telethon_client import telethon_client
from telethon.tl.functions.channels import GetFullChannelRequest
from database import get_all_group_ids, _db_pool

async def update_group_metadata():
    if not telethon_client.is_connected():
        await telethon_client.connect()
    group_ids = get_all_group_ids()
    for chat_id in group_ids:
        try:
            entity = await telethon_client.get_entity(chat_id)
            full   = await telethon_client(GetFullChannelRequest(entity.username or entity.id))
            title = getattr(entity, "title", None)
            description = getattr(full.full_chat, "about", None)
            members = getattr(full.full_chat, "participants_count", None)
            admins = len(getattr(full.full_chat, "admin_rights", []) or [])
            topics = getattr(full.full_chat, "forum_info", {}).get("total_count", None)
            # Beispiel: weitere Felder wie bots kannst du hier ergänzen

            conn = _db_pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE group_settings
                        SET title=%s, description=%s, member_count=%s, admin_count=%s, topic_count=%s
                        WHERE chat_id=%s
                    """, (title, description, members, admins, topics, chat_id))
                    conn.commit()
            finally:
                _db_pool.putconn(conn)
            print(f"Gruppe {chat_id}: aktualisiert.")
        except Exception as e:
            print(f"Fehler bei {chat_id}: {e}")

if __name__ == "__main__":
    asyncio.run(update_group_metadata())