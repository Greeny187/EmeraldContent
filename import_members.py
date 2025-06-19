import asyncio
import time
from telethon import TelegramClient
from database import add_member  # eure Funktion zum Einfügen

# Ersetze diese Werte mit deinen API-Credentials
api_id = 29370987
api_hash = 'd3c4c05db902fbefb7944e13c1a97afa'

# ID oder Username der Zielgruppe
TARGET = '-1002331923014'

# Timestamp für „jetzt“:
now_ts = int(time.time())

client = TelegramClient('one_time_session', api_id, api_hash)

async def main():
    await client.start()
    print(f"Hole Teilnehmer aus {TARGET}…")
    async for user in client.iter_participants(TARGET):
        # user.id enthält die Telegram-User-ID
        try:
            add_member(chat_id=user.chat_id if hasattr(user, 'chat_id') else client.get_entity(TARGET).id,
                       user_id=user.id,
                       joined_at=now_ts)
        except Exception as e:
            print(f"Fehler beim Einfügen von {user.id}: {e}")
    print("Import abgeschlossen!")
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
