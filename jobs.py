from telegram.ext import JobQueue
from datetime import date, time
from zoneinfo import ZoneInfo
from database import get_registered_groups, get_group_stats, is_daily_stats_enabled
import logging

logger = logging.getLogger(__name__)

async def daily_report(context):
    """
    Sendet jeden Morgen um 08:00 Uhr Berlin-Zeit die Top-3-Statistik
    (Nachrichten-Zähler) an alle registrierten Gruppen.
    """
    today = date.today()
    bot = context.bot
    for chat_id, _ in get_registered_groups():
        # Nur Gruppen mit aktivierten Tagesstatistiken berücksichtigen
        if not is_daily_stats_enabled(chat_id):
            continue
        try:
            top3 = get_group_stats(chat_id, today)  # List[Tuple[user_id, count]]
            if not top3:
                continue

            # Baue die Nachricht
            lines = [
                f"{i+1}. <a href='tg://user?id={uid}'>User</a>: {cnt} Nachrichten"
                for i, (uid, cnt) in enumerate(top3)
            ]
            text = (
                f"📊 *Tagesstatistik {today.isoformat()}*\n"
                "📝 Top 3 aktive Mitglieder:\n" +
                "\n".join(lines)
            )
            await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Fehler beim Senden der Tagesstatistik an {chat_id}: {e}")


def register_jobs(app):
    """
    Registriert alle geplanten Jobs im Bot.
    """
    jq: JobQueue = app.job_queue
    # Täglich um 08:00 Berlin-Zeit
    jq.run_daily(
        daily_report,
        time=time(hour=8, minute=0, tzinfo=ZoneInfo("Europe/Berlin"))
    )
