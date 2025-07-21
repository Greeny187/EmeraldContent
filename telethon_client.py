import os
from telethon import TelegramClient
from telethon.sessions import StringSession
# Beachte: SESSION optional für Import, Raise erfolgt in start_telethon()

# Telegram-API-Zugangsdaten aus den Umgebungsvariablen
API_ID   = int(os.getenv("TG_API_ID"))
API_HASH = os.getenv("TG_API_HASH")
# Eine StringSession für einen Benutzeraccount ist notwendig, da Bots keine GetHistoryRequests erlauben
SESSION  = os.getenv("TELETHON_SESSION")  # StringSession für Benutzer-Login

# Client-Instanz erzeugen (StringSession speichert Login-Daten)
telethon_client = TelegramClient(StringSession(SESSION) if SESSION else StringSession(), API_ID, API_HASH)
("TELETHON_SESSION")

if not all([API_ID, API_HASH, SESSION]):
    raise RuntimeError("TG_API_ID, TG_API_HASH und TELETHON_SESSION müssen als Env-Vars gesetzt sein!")

# Client-Instanz erzeugen (StringSession speichert Login-Daten)
telethon_client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)

async def start_telethon():
    """Stellt die Verbindung her und prüft die Autorisierung."""
    if not SESSION:
        raise RuntimeError("Die Umgebungsvariable TELETHON_SESSION ist nicht gesetzt. Bitte SESSION erzeugen und in Heroku Config hinzufügen!")
    await telethon_client.connect()
    if not await telethon_client.is_user_authorized():
        raise RuntimeError("Telethon-Client ist nicht autorisiert! Bitte valide TELETHON_SESSION setzen.")()
    """Stellt eine Verbindung mit dem Telegram-API-Server her und überprüft die Autorisierung."""
    await telethon_client.connect()
    if not await telethon_client.is_user_authorized():
        raise RuntimeError("Telethon-Client ist nicht autorisiert! Bitte SESSION prüfen.")