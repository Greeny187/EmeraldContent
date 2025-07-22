import asyncio
from telethon_client import telethon_client
from telethon.tl.functions.channels import GetFullChannelRequest
from database import get_all_group_ids, _db_pool

async def update_titles_and_descriptions():
    group_ids = get_all_group_ids()
    for chat_id in group_ids:
        try:
            entity = await telethon_client.get_entity(chat_id)
            full   = await telethon_client(GetFullChannelRequest(entity.username or entity.id))
            title = getattr(entity, "title", None)
            description = getattr(full.full_chat, "about", None)
            conn = _db_pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE group_settings
                        SET title=%s, description=%s
                        WHERE chat_id=%s
                    """, (title, description, chat_id))
                    conn.commit()
            finally:
                _db_pool.putconn(conn)
            print(f"Gruppe {chat_id}: aktualisiert.")
        except Exception as e:
            print(f"Fehler bei {chat_id}: {e}")

if __name__ == "__main__":
    asyncio.run(update_titles_and_descriptions())