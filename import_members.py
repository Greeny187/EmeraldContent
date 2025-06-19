import asyncio
import time
import sys
from telethon import TelegramClient
from database import add_member

# 1) Trage hier deine Credentials ein:
api_id   = 29370987                # von https://my.telegram.org
api_hash = 'd3c4c05db902fbefb7944e13c1a97afa' # von https://my.telegram.org

# 2) Lies Chat-ID oder Username aus den Argumenten:
if len(sys.argv) != 2:
    print("Usage: python import_members_group.py <chat_id_or_username>")
    sys.exit(1)
TARGET = sys.argv[1]

# 3) Setup Telethon-Client
client = TelegramClient('one_time_session', api_id, api_hash)

async def main():
    await client.start()
    now_ts = int(time.time())
    print(f"Hole Teilnehmer aus {TARGET}…")

    # Entity laden (funktioniert für ID, @username oder t.me/Link)
    entity = await client.get_entity(TARGET)

    # Alle Teilnehmer durchlaufen und in die DB schreiben
    async for user in client.iter_participants(entity):
        try:
            add_member(
                chat_id   = entity.id,
                user_id   = user.id,
                joined_at = now_ts
            )
        except Exception as e:
            print(f"  Fehler beim Hinzufügen von {user.id}: {e}")

    print("Import abgeschlossen!")
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())