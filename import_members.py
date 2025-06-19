import asyncio
import os
import argparse
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

# Ersetze diese Werte mit deinen API-Credentials
api_id = 29370987
api_hash = 'd3c4c05db902fbefb7944e13c1a97afa'
BOT_TOKEN = os.getenv("BOT_TOKEN")

async def import_members(group_identifier: str):
    """
    Liest alle Mitglieder der angegebenen Gruppe/Channel ein und gibt sie auf der Konsole aus.
    group_identifier kann eine ID (z.B. -1001234567890) oder ein Username (@mein_channel) sein.
    """
    client = await TelegramClient('bot', api_id, api_hash).start(bot_token=BOT_TOKEN)
    try:
        # TelegramClient.get_entity erkennt automatisch Chat, Supergroup oder Channel
        entity = await client.get_entity(group_identifier)
    except Exception as e:
        print(f"Fehler beim Laden der Gruppe '{group_identifier}': {e}")
        await client.disconnect()
        return

    print(f"Importiere Mitglieder von: {entity.title or entity.username} ({group_identifier})")
    count = 0
    async for user in client.iter_participants(entity):
        print(user.id, user.username or "-", user.first_name or "-", user.last_name or "-")
        count += 1

    print(f"Fertig! Insgesamt {count} Mitglieder gefunden.\n")
    await client.disconnect()

async def main():
    parser = argparse.ArgumentParser(description="Importiere Telegram-Mitglieder aus einer Gruppe/einem Channel")
    parser.add_argument(
        "--group", "-g",
        required=True,
        help="ID (z.B. -1001234567890) oder Username (z.B. @mein_channel) der Zielgruppe"
    )
    args = parser.parse_args()

    # Frage zur manuellen Bestätigung
    confirm = input(f"Möchtest du die Mitglieder der Gruppe '{args.group}' importieren? [j/N] ")
    if confirm.lower() != 'j':
        print("Abgebrochen.")
        return

    await import_members(args.group)

if __name__ == '__main__':
    asyncio.run(main())