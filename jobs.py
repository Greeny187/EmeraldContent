import os
import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import date, time
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, JobQueue
from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from database import _db_pool, get_registered_groups, is_daily_stats_enabled, purge_deleted_members, get_group_stats
from statistic import fetch_and_store_stats

logger = logging.getLogger(__name__)

# === Configuration ===
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_NAME = 'userbot_session'
CHANNEL_USERNAMES = [u.strip() for u in os.getenv("STATS_CHANNELS", "").split(",") if u.strip()]
DEVELOPER_IDS = {int(x) for x in os.getenv("DEVELOPER_CHAT_IDS", "").split(",") if x.strip().isdigit()}
TIMEZONE = os.getenv("TZ", "Europe/Berlin")

# === Telethon Client ===
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# === Helpers ===
def get_db_connection():
    conn = _db_pool.getconn()
    conn.autocommit = True
    return conn

# === Job Functions ===
async def daily_report(context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    bot = context.bot
    for chat_id, _ in get_registered_groups():
        if not is_daily_stats_enabled(chat_id):
            continue
        try:
            top3 = []
            # Annahme: get_group_stats liefert List[Tuple[user_id, count]]
            top3 = get_group_stats(chat_id, today)
            if not top3:
                continue
            lines = [
                f"{i+1}. <a href='tg://user?id={uid}'>User</a>: {cnt} Nachrichten"
                for i, (uid, cnt) in enumerate(top3)
            ]
            text = (
                f"📊 *Tagesstatistik {today.isoformat()}*\n"
                f"📝 Top 3 aktive Mitglieder:\n" + "\n".join(lines)
            )
            await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Fehler beim Senden der Tagesstatistik an {chat_id}: {e}")

async def telethon_stats_job(context: ContextTypes.DEFAULT_TYPE):
    for username in CHANNEL_USERNAMES:
        try:
            await client.start()
            full = await client(GetFullChannelRequest(username))
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO daily_stats (chat_id, stat_date, members, admins)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (chat_id, stat_date)
                    DO UPDATE SET members = EXCLUDED.members, admins = EXCLUDED.admins;
                    """,
                    (
                        full.chats[0].id,
                        date.today(),
                        full.full_chat.participants_count,
                        len(full.full_chat.admin_rights or [])
                    )
                )
            _db_pool.putconn(conn)
            await client.disconnect()
        except Exception as e:
            logger.error(f"Fehler beim Abfragen von {username}: {e}")

async def purge_members_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        purge_deleted_members()
        logger.info("Purge von gelöschten Mitgliedern abgeschlossen.")
    except Exception as e:
        logger.error(f"Fehler beim Purgen von Mitgliedern: {e}")

def schedule_daily_stats(chat_ids: list[int], hour: int = 3):
    sched = AsyncIOScheduler()
    for cid in chat_ids:
        sched.add_job(
            lambda c=cid: asyncio.create_task(fetch_and_store_stats(c)),
            trigger='cron', hour=hour, minute=0
        )
    sched.start()

# === Scheduler Registration ===
def register_jobs(app):
    jq: JobQueue = app.job_queue
    # Täglich um 08:00 Berlin-Zeit
    jq.run_daily(
        daily_report,
        time(hour=8, minute=0, tzinfo=ZoneInfo(TIMEZONE)),
        name="daily_report"
    )
    # Täglich um 02:00 Berlin-Zeit Telethon Stats
    jq.run_daily(
        telethon_stats_job,
        time(hour=2, minute=0, tzinfo=ZoneInfo(TIMEZONE)),
        name="telethon_stats"
    )
    # Täglich um 03:00 Berlin-Zeit Mitglieder-Purge
    jq.run_daily(
        purge_members_job,
        time(hour=3, minute=0, tzinfo=ZoneInfo(TIMEZONE)),
        name="purge_members"
    )
    logger.info("Jobs registriert: daily_report, telethon_stats, purge_members")

# === Entrypoint for standalone run ===
if __name__ == "__main__":
    # Für lokalen Test
    from telegram.ext import Application
    import asyncio
    app = Application.builder().token(os.getenv("BOT_TOKEN")).build()
    register_jobs(app)
    # Start Polling für lokalen Lauf
    app.run_polling()
