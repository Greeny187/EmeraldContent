import asyncio
from telethon import TelegramClient
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import InputPeerChat
import os

# Ersetze diese Werte mit deinen API-Credentials
api_id = 29370987
api_hash = 'd3c4c05db902fbefb7944e13c1a97afa'
BOT_TOKEN = os.getenv("BOT_TOKEN")
# ID oder Username der Zielgruppe
TARGET = -1002331923014

# Deine Gruppen-ID als Integer
TARGET = 123456789

async def main():
    # Client bauen *und* starten in einem Schritt – das await liefert dir den fertigen Client zurück
    client = await TelegramClient('bot', api_id, api_hash).start(bot_token=BOT_TOKEN)

    full = await client(GetFullChatRequest(chat_id=TARGET))
    peer = InputPeerChat(chat_id=TARGET)
    async for user in client.iter_participants(peer):
        print(user.id, user.username, user.first_name)

    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())