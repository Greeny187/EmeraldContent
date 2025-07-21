import os
import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import date, time
from zoneinfo import ZoneInfo
from telegram.ext import ContextTypes, JobQueue
from telethon_client import telethon_client
from telethon.tl.functions.channels import GetFullChannelRequest
from database import _db_pool, get_registered_groups, is_daily_stats_enabled, purge_deleted_members, get_group_stats

logger = logging.getLogger(__name__)
CHANNEL_USERNAMES = [u.strip() for u in os.getenv("STATS_CHANNELS", "").split(",") if u.strip()]
TIMEZONE = os.getenv("TZ", "Europe/Berlin")

# === Job Functions ===
async def daily_report(context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    bot = context.bot
    for chat_id, _ in get_registered_groups():
        if not is_daily_stats_enabled(chat_id):
            continue
        try:
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
    # Telethon-Client direkt aus telethon_client.py
    # Sicherstellen, dass der Telethon-Client verbunden ist
    if not telethon_client.is_connected():
        await telethon_client.connect()
    for username in CHANNEL_USERNAMES:
        try:
            full = await telethon_client(GetFullChannelRequest(username))
            conn = _db_pool.getconn()
            conn.autocommit = True
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
        except Exception as e:
            logger.error(f"Fehler beim Abfragen von {username}: {e}")

async def purge_members_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        purge_deleted_members()
        logger.info("Purge von gelöschten Mitgliedern abgeschlossen.")
    except Exception as e:
        logger.error(f"Fehler beim Purgen von Mitgliedern: {e}")

# === Scheduler Registration ===
def register_jobs(app):
    jq: JobQueue = app.job_queue
    jq.run_daily(
        daily_report,
        time(hour=8, minute=0, tzinfo=ZoneInfo(TIMEZONE)),
        name="daily_report"
    )
    jq.run_daily(
        telethon_stats_job,
        time(hour=2, minute=0, tzinfo=ZoneInfo(TIMEZONE)),
        name="telethon_stats"
    )
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
