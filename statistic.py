import re
import os
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database import _with_cursor, _db_pool

logger = logging.getLogger(__name__)

# --- Telethon-Setup ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_NAME = 'userbot_session'
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# Hilfsfunktion für rohe DB-Verbindung
def get_db_connection():
    conn = _db_pool.getconn()
    conn.autocommit = True
    return conn

# Deine Developer-IDs für globale Metriken
raw = os.getenv("DEVELOPER_CHAT_IDS", "")
DEVELOPER_IDS = {int(x) for x in raw.split(",") if x.strip().isdigit()}

# --- Telethon-Daten abrufen und speichern ---
async def fetch_and_store_stats(chat_username: str):
    await client.start()
    full = await client(GetFullChannelRequest(chat_username))
    conn = get_db_connection()
    try:
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
                    datetime.utcnow().date(),
                    full.full_chat.participants_count,
                    len(full.full_chat.admin_rights or [])
                )
            )
    finally:
        _db_pool.putconn(conn)
    await client.disconnect()

# Scheduler für nächtliche Abfragen
def schedule_telethon_jobs(chat_usernames: list[str]):
    scheduler = AsyncIOScheduler()
    for username in chat_usernames:
        scheduler.add_job(lambda u=username: fetch_and_store_stats(u), trigger='cron', hour=2, minute=0)
    scheduler.start()

# --- Schema-Migration für Stats ---
@_with_cursor
def init_stats_db(cur):
    cur.execute(
        """
        ALTER TABLE group_settings
        ADD COLUMN IF NOT EXISTS last_command TEXT;
        """
    )
    cur.execute(
        """
        ALTER TABLE group_settings
        ADD COLUMN IF NOT EXISTS last_active TIMESTAMPTZ;
        """
    )
    cur.execute(
        """
        ALTER TABLE group_settings
        ADD COLUMN IF NOT EXISTS group_activity_score REAL DEFAULT 0;
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS command_logs (
            chat_id BIGINT,
            user_id BIGINT,
            command TEXT,
            used_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_command_logs_chat ON command_logs(chat_id);")

# --- Befehls-Logging ---
@_with_cursor
def log_command(cur, chat_id: int, user_id: int, command: str):
    cur.execute(
        "INSERT INTO command_logs (chat_id, user_id, command) VALUES (%s, %s, %s);",
        (chat_id, user_id, command)
    )
    cur.execute(
        "UPDATE group_settings SET last_command = %s, last_active = CURRENT_TIMESTAMP WHERE chat_id = %s;",
        (command, chat_id)
    )

# --- Metrik-Funktionen ---
@_with_cursor
def get_active_users_count(cur, chat_id: int, start_date: datetime, end_date: datetime) -> int:
    cur.execute(
        "SELECT COUNT(DISTINCT user_id) FROM daily_stats WHERE chat_id = %s AND stat_date BETWEEN %s AND %s;",
        (chat_id, start_date.date(), end_date.date())
    )
    result = cur.fetchone()[0]
    return result or 0

@_with_cursor
def get_command_usage(cur, chat_id: int, start_date: datetime, end_date: datetime):
    cur.execute(
        "SELECT command, COUNT(*) AS count FROM command_logs "
        "WHERE chat_id = %s AND used_at::date BETWEEN %s AND %s "
        "GROUP BY command ORDER BY count DESC;",
        (chat_id, start_date.date(), end_date.date())
    )
    return cur.fetchall()

@_with_cursor
def get_activity_by_weekday(cur, chat_id: int, start_date: datetime, end_date: datetime):
    cur.execute(
        "SELECT EXTRACT(DOW FROM stat_date) AS weekday, SUM(messages) AS total "
        "FROM daily_stats "
        "WHERE chat_id = %s AND stat_date BETWEEN %s AND %s "
        "GROUP BY weekday ORDER BY weekday;",
        (chat_id, start_date.date(), end_date.date())
    )
    return cur.fetchall()

@_with_cursor
def get_top_groups(cur, start_date: datetime, end_date: datetime, limit: int = 5):
    cur.execute(
        "SELECT chat_id, SUM(messages) AS total_msgs FROM daily_stats "
        "WHERE stat_date BETWEEN %s AND %s "
        "GROUP BY chat_id ORDER BY total_msgs DESC LIMIT %s;",
        (start_date.date(), end_date.date(), limit)
    )
    return cur.fetchall()

# --- Stats-Command ---
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    params = {}
    for arg in args:
        if '=' in arg:
            k, v = arg.split('=', 1)
            params[k.lower()] = v

    group_id = int(params.get('group', update.effective_chat.id))
    range_str = params.get('range', '7d')
    is_dev = update.effective_user.id in DEVELOPER_IDS

    m = re.match(r"(\d+)([dw])", range_str)
    days = int(m.group(1)) * (7 if m and m.group(2) == 'w' else 1) if m else 7
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=days)

    active_users = get_active_users_count(group_id, start_dt, end_dt)
    weekday_activity = get_activity_by_weekday(group_id, start_dt, end_dt)

    text = f"📊 *Statistiken für Gruppe {group_id}*\n"
    text += f"🗓 Zeitraum: {start_dt.date()} bis {end_dt.date()}\n"
    text += f"• Aktive Nutzer: `{active_users}`\n"
    text += "• Aktivität pro Wochentag (0=So):\n"
    for wd, total in weekday_activity:
        text += f"   – {int(wd)}: {int(total)} Nachrichten\n"

    await update.effective_message.reply_text(text, parse_mode='Markdown')

# --- Handler-Registrierung ---
def register_statistics_handlers(app):
    init_stats_db()
    app.add_handler(CommandHandler(['stats', 'statistik'], stats_command), group=10)
    async def command_logger(update: Update, context: ContextTypes.DEFAULT_TYPE):
        cmd = update.effective_message.text.split()[0].lstrip('/')
        log_command(update.effective_chat.id, update.effective_user.id, cmd)
    app.add_handler(MessageHandler(filters.COMMAND & ~filters.Regex(r'^/stats'), command_logger), group=9)

