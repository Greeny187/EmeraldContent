import os
import datetime
import logging
import statistic
import asyncio
from telegram.ext import filters, MessageHandler, Application, PicklePersistence
from telethon_client import telethon_client, start_telethon
from telethon import TelegramClient
from handlers import register_handlers, error_handler
from menu import register_menu
from rss import register_rss
from database import init_all_schemas
from logger import setup_logging
from mood import register_mood
from jobs import register_jobs
from request_config import create_request_with_increased_pool
from ads import init_ads_schema, register_ads, register_ads_jobs
from devmenu import register_dev_handlers
from statistic import register_statistics_handlers

# Env-Variablen prüfen
API_ID    = os.getenv("TG_API_ID")
API_HASH  = os.getenv("TG_API_HASH")
SESSION   = os.getenv("TELETHON_SESSION")
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not all([API_ID, API_HASH, SESSION, BOT_TOKEN]):
    raise RuntimeError(
        "TG_API_ID, TG_API_HASH, TELETHON_SESSION und BOT_TOKEN müssen als Env-Vars gesetzt sein!"
    )
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL ist nicht gesetzt.")
PORT = int(os.getenv("PORT", 8443))

async def log_update(update, context):
    logging.debug(f"Update angekommen: {update}")

# Diese Funktionen hinzufügen:
async def post_init(application: Application) -> None:
    """
    Wird nach der Initialisierung der Application aufgerufen.
    Kann für zusätzliche Einrichtungsschritte verwendet werden.
    """
    logging.info("Bot wurde initialisiert und ist jetzt betriebsbereit.")
    await application.bot.send_message(chat_id=os.getenv("DEVELOPER_CHAT_ID", "5114518219"),
                              text=f"🤖 Der Bot wurde neu gestartet und ist jetzt online.")

async def post_shutdown(application: Application) -> None:
    """
    Wird aufgerufen, bevor die Application vollständig heruntergefahren wird.
    Kann für Aufräumarbeiten verwendet werden.
    """
    logging.info("Bot wird heruntergefahren.")
    # Wenn nötig, hier zusätzliche Ressourcen freigeben
    try:
        await application.bot.send_message(chat_id=os.getenv("DEVELOPER_CHAT_ID", "5114518219"),
                                  text="🛑 Der Bot wird heruntergefahren.")
    except:
        pass  # Beim Herunterfahren ignorieren wir Fehler beim Senden

async def shutdown(signal, loop, client: TelegramClient, app: Application):
    # Stoppe Telegram-Handlers
    await app.stop()
    # Trenne Telethon-Client
    await client.disconnect()
    # Alle übrigen Tasks abbrechen
    tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task(loop)]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

def main():
    setup_logging()
    init_all_schemas()
    init_ads_schema()  # <- Hinzufügen
    statistic.init_stats_db()

    # Telethon (User-Session) verbinden
    asyncio.get_event_loop().run_until_complete(start_telethon())

    # Erstelle eine Application mit angepasstem Request-Objekt
    persistence = PicklePersistence(filepath="state.pickle")
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .request(create_request_with_increased_pool())
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_error_handler(error_handler)
    app.add_handler(MessageHandler(filters.ALL, log_update), group=-2)
    
    # Handler-Reihenfolge korrigieren:
    register_mood(app)      # group=0 (Mood-Commands) - FRÜHER
    register_handlers(app)  # group=0 (Commands)
    
    register_statistics_handlers(app)  # group=0 (Statistics-Commands)
    register_menu(app)      # group=1 (Menu-Replies, keine Commands)
    register_rss(app)       # group=3 (RSS-spezifisch)
    register_ads(app)       # <- Hinzufügen
    register_dev_handlers(app)  # group=4 (Entwickler-Commands)
    # Jobs registrieren
    register_jobs(app)
    register_ads_jobs(app)  # <- Hinzufügen

    # Startzeit und Telethon-Client speichern
    app.bot_data['start_time'] = datetime.datetime.now()
    app.bot_data['telethon_client'] = telethon_client

    # Webhook starten
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=f"/webhook/{BOT_TOKEN}",
        webhook_url=WEBHOOK_URL,
        drop_pending_updates=False,
        max_connections=40
    )

if __name__ == "__main__":
    main()