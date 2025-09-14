import asyncio
import os
import argparse
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
from shared.database import add_member

# Ersetze diese Werte mit deinen API-Credentials
api_id = 29370987
api_hash = 'd3c4c05db902fbefb7944e13c1a97afa'
BOT_TOKEN = os.getenv("BOT_TOKEN")

async def list_chats():
    client = await TelegramClient('bot', api_id, api_hash).start(bot_token=BOT_TOKEN)
    print("VerfÃ¼gbare Chats (Gruppen/KanÃ¤le):")
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if getattr(entity, 'megagroup', False) or getattr(entity, 'broadcast', False) or getattr(entity, 'gigagroup', False):
            title = getattr(entity, 'title', None) or getattr(entity, 'username', None) or str(entity.id)
            username = f"@{entity.username}" if getattr(entity, 'username', None) else "-"
            print(f" â€¢ {title:30} | ID: {entity.id:>15} | Username: {username}")
    await client.disconnect()

async def import_members(group_identifier: str):
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(os.getenv("SESSION_STRING")), api_id, api_hash)
    await client.start()
    
    try:
        identifier = (
            int(group_identifier)
            if group_identifier.lstrip('-').isdigit()
            else group_identifier
        )
        entity = await client.get_entity(identifier)
    except Exception as e:
        print(f"Fehler beim Laden der Gruppe '{group_identifier}': {e}")
        await client.disconnect()
        return

    # Bestimme die richtige chat_id fÃ¼r die DB
    if isinstance(entity, Channel):
        chat_id_db = int(f"-100{entity.id}")
    else:
        chat_id_db = entity.id

    print(f"\nImportiere Mitglieder von: {entity.title or entity.username} (DB chat_id={chat_id_db})\n")
    count = 0
    async for user in client.iter_participants(entity):
        add_member(chat_id_db, user.id)
        print(f"âœ… {user.id:<10} {user.username or '-':<20} wurde gespeichert (chat_id={chat_id_db}).")
        count += 1

    print(f"\nFertig! Insgesamt {count} Mitglieder gespeichert.")
    await client.disconnect()

async def main():
    parser = argparse.ArgumentParser(
        description="Importiere Telegram-Mitglieder und speichere sie in der vorhandenen Datenbank"
    )
    parser.add_argument("--list", action="store_true", help="Liste alle verfÃ¼gbaren Gruppen/KanÃ¤le auf")
    parser.add_argument("--group", "-g", help="ID oder Username (z.B. @channel) der Gruppe")
    parser.add_argument("--yes", action="store_true", help="BestÃ¤tigt den Import automatisch (fÃ¼r nicht-interaktive Umgebungen)")
    args = parser.parse_args()

    if args.list:
        await list_chats()
        return

    if not args.group:
        print("Nutze --list, um zuerst alle Chats aufzulisten, oder gib mit --group eine ID/Username an.")
        return

    if not args.yes:
        confirm = input(f"MÃ¶chtest du die Mitglieder der Gruppe '{args.group}' importieren und in der DB speichern? [j/N] ")
        if confirm.lower() != 'j':
            print("Abgebrochen.")
            return
    
    await import_members(args.group)

if __name__ == '__main__':
    asyncio.run(main())

