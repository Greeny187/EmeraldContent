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
    # 1) Client erzeugen, aber nicht .start() ankettieren
    client = TelegramClient('bot', api_id, api_hash)

    # 2) Start awaiten – jetzt ist client eine laufende Instanz
    await client.start(bot_token=BOT_TOKEN)

    # 3) FullChat abfragen
    full = await client(GetFullChatRequest(chat_id=TARGET))

    # 4) Peer bauen und Teilnehmer iterieren
    peer = InputPeerChat(chat_id=TARGET)
    async for user in client.iter_participants(peer):
        print(user.id, user.username, user.first_name)

    # 5) Sauber trennen
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())