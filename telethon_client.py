import os
from telethon import TelegramClient
from telethon.sessions import StringSession

# Telegram-API-Zugangsdaten aus den Umgebungsvariablen
API_ID   = int(os.getenv("TG_API_ID"))
API_HASH = os.getenv("TG_API_HASH")
# Eine StringSession für einen Benutzeraccount ist notwendig, da Bots keine GetHistoryRequests erlauben
SESSION  = os.getenv("TELETHON_SESSION")

if not all([API_ID, API_HASH, SESSION]):
    raise RuntimeError("TG_API_ID, TG_API_HASH und TELETHON_SESSION müssen als Env-Vars gesetzt sein!")

# Client-Instanz erzeugen (StringSession speichert Login-Daten)
telethon_client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)

async def start_telethon():
    """Stellt eine Verbindung mit dem Telegram-API-Server her und überprüft die Autorisierung."""
    await telethon_client.connect()
    if not await telethon_client.is_user_authorized():
        raise RuntimeError("Telethon-Client ist nicht autorisiert! Bitte SESSION prüfen.")