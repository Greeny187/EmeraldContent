import asyncio
import os
from telethon import TelegramClient
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import InputPeerChat

# Ersetze diese Werte mit deinen API-Credentials
api_id = 29370987
api_hash = 'd3c4c05db902fbefb7944e13c1a97afa'
BOT_TOKEN = os.getenv("BOT_TOKEN")
# ID oder Username der Zielgruppe
TARGET = -1002331923014

async def main():
    client = TelegramClient('bot', api_id, api_hash).start(BOT_TOKEN)

    # 1) FullChat abfragen
    full = await client(GetFullChatRequest(chat_id=TARGET))
    # (Du kannst full.chats[0] inspizieren, wenn du mehr Meta-Daten brauchst)

    # 2) InputPeerChat erzeugen
    peer = InputPeerChat(chat_id=TARGET)

    # 3) Teilnehmer durchlaufen
    async for user in client.iter_participants(peer):
        print(user.id, user.username, user.first_name)

    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())