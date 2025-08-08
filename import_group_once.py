import asyncio
from telegram import Bot
from database import _with_cursor
import os

# Bot-Token aus Umgebungsvariable (wie bei Heroku üblich)
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN nicht gesetzt")

# Chat-ID manuell eintragen oder als Umgebungsvariable
CHAT_ID = 5774568909

async def import_group():
    bot = Bot(TOKEN)
    try:
        chat = await bot.get_chat(CHAT_ID)
        member_count = await bot.get_chat_member_count(CHAT_ID)
        title = chat.title
        description = chat.description or ""
        save_group_metadata(CHAT_ID, title, description, member_count)
        print(f"✅ Gruppe gespeichert: {title} ({CHAT_ID}) mit {member_count} Mitgliedern")
    except Exception as e:
        print(f"❌ Fehler: {e}")

@_with_cursor
def save_group_metadata(cur, chat_id: int, title: str, description: str, member_count: int):
    cur.execute("""
        INSERT INTO group_settings (chat_id, title, description, member_count)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (chat_id)
        DO UPDATE SET
          title = EXCLUDED.title,
          description = EXCLUDED.description,
          member_count = EXCLUDED.member_count;
    """, (chat_id, title, description, member_count))


if __name__ == "__main__":
    asyncio.run(import_group())
