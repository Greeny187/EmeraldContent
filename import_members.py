import asyncio
import os
import argparse
from telethon import TelegramClient
from database import add_member

# Ersetze diese Werte mit deinen API-Credentials
api_id = 29370987
api_hash = 'd3c4c05db902fbefb7944e13c1a97afa'
BOT_TOKEN = os.getenv("BOT_TOKEN")

async def list_chats():
    client = await TelegramClient('bot', api_id, api_hash).start(bot_token=BOT_TOKEN)
    print("Verfügbare Chats (Gruppen/Kanäle):")
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        # Nur Gruppen und Kanäle
        if getattr(entity, 'megagroup', False) or getattr(entity, 'broadcast', False) or getattr(entity, 'gigagroup', False):
            title = getattr(entity, 'title', None) or getattr(entity, 'username', None) or str(entity.id)
            username = f"@{entity.username}" if getattr(entity, 'username', None) else "-"
            print(f" • {title:30} | ID: {entity.id:>15} | Username: {username}")
    await client.disconnect()

async def import_members(group_identifier: str):
    client = await TelegramClient('bot', api_id, api_hash).start(bot_token=BOT_TOKEN)
    try:
        # Wenn der Identifier eine reine Zahl ist, suche über Dialoge
        if group_identifier.lstrip('-').isdigit():
            target = None
            async for dialog in client.iter_dialogs():
                if dialog.entity.id == int(group_identifier):
                    target = dialog.entity
                    break
            if not target:
                raise ValueError("ID nicht in deinen Chats gefunden.")
            entity = target
        else:
            # Username oder Link
            entity = await client.get_entity(group_identifier)
    except Exception as e:
        print(f"Fehler beim Laden der Gruppe '{group_identifier}': {e}")
        await client.disconnect()
        return

    print(f"\nImportiere Mitglieder von: {entity.title or entity.username} ({group_identifier})\n")
    count = 0
    async for user in client.iter_participants(entity):
        # Speichere in der Datenbank
        add_member(entity.id, user.id)
        print(f"✅ {user.id:<10} {user.username or '-':<20} wurde gespeichert.")
        count += 1

    print(f"\nFertig! Insgesamt {count} Mitglieder gespeichert.")
    await client.disconnect()

async def main():
    parser = argparse.ArgumentParser(description="Importiere Telegram-Mitglieder und speichere sie in der vorhandenen Datenbank")
    parser.add_argument("--list", action="store_true", help="Liste alle verfügbaren Gruppen/Kanäle auf")
    parser.add_argument("--group", "-g", help="ID oder Username (z.B. @channel) der Gruppe")
    args = parser.parse_args()

    if args.list:
        await list_chats()
        return

    if not args.group:
        print("Nutze --list, um zuerst alle Chats aufzulisten, oder gib mit --group eine ID/Username an.")
        return

    confirm = input(f"Möchtest du die Mitglieder der Gruppe '{args.group}' importieren und in der DB speichern? [j/N] ")
    if confirm.lower() != 'j':
        print("Abgebrochen.")
        return

    await import_members(args.group)

if __name__ == '__main__':
    asyncio.run(main())
