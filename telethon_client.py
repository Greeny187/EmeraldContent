import os
from telethon import TelegramClient

# Telegram-API-Zugangsdaten aus den Env-Variablen laden
API_ID   = int(os.getenv("TG_API_ID"))
API_HASH = os.getenv("TG_API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not all([API_ID, API_HASH, BOT_TOKEN]):
    raise RuntimeError("TG_API_ID, TG_API_HASH und BOT_TOKEN müssen gesetzt sein!")

# Client-Instanz erstellen (Session-Datei 'bot_session')
telethon_client = TelegramClient('bot_session', API_ID, API_HASH)

async def start_telethon():
    """Starte Telethon mit dem Bot-Token."""
    await telethon_client.start(bot_token=BOT_TOKEN)