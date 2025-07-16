import re
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from database import _with_cursor

logger = logging.getLogger(__name__)

# --- Schema-Migration für neue Spalten und Log-Tabelle ---
@_with_cursor
def init_stats_db(cur):
    # Neue Spalten in group_settings
    cur.execute("""
        ALTER TABLE group_settings
        ADD COLUMN IF NOT EXISTS last_command TEXT;
    """)
    cur.execute("""
        ALTER TABLE group_settings
        ADD COLUMN IF NOT EXISTS last_active TIMESTAMPTZ;
    """)
    cur.execute("""
        ALTER TABLE group_settings
        ADD COLUMN IF NOT EXISTS group_activity_score REAL DEFAULT 0;
    """)
    # Tabelle für Loggen von Befehlen
    cur.execute("""
        CREATE TABLE IF NOT EXISTS command_logs (
            chat_id BIGINT,
            user_id BIGINT,
            command TEXT,
            used_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Index für schnellere Abfragen
    cur.execute("CREATE INDEX IF NOT EXISTS idx_command_logs_chat ON command_logs(chat_id);")

# --- Logging-Funktion für jede Befehlsnutzung ---
@_with_cursor
def log_command(cur, chat_id: int, user_id: int, command: str):
    cur.execute(
        "INSERT INTO command_logs (chat_id, user_id, command) VALUES (%s, %s, %s);",
        (chat_id, user_id, command)
    )
    # Aktualisiere letzte Aktivität in group_settings
    cur.execute(
        "UPDATE group_settings SET last_command = %s, last_active = CURRENT_TIMESTAMP WHERE chat_id = %s;",
        (command, chat_id)
    )

# --- Metrik-Funktionen ---
@_with_cursor
def get_active_users_count(cur, chat_id: int, start_date: datetime, end_date: datetime) -> int:
    cur.execute(
        "SELECT COUNT(DISTINCT user_id) FROM daily_stats "
        "WHERE chat_id = %s AND stat_date BETWEEN %s AND %s;",
        (chat_id, start_date.date(), end_date.date())
    )
    result = cur.fetchone()[0]
    return result if result is not None else 0

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
        "SELECT chat_id, SUM(messages) AS total_msgs "
        "FROM daily_stats "
        "WHERE stat_date BETWEEN %s AND %s "
        "GROUP BY chat_id ORDER BY total_msgs DESC LIMIT %s;",
        (start_date.date(), end_date.date(), limit)
    )
    return cur.fetchall()

# --- Command-Handler für /stats und /statistik ---
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Parameter parsen: group=<id> range=<Nd|Nw>
    params = {}
    for arg in context.args:
        if '=' in arg:
            k, v = arg.split('=', 1)
            params[k.lower()] = v

    # Standardwerte
    group_id = int(params.get('group', update.effective_chat.id))
    range_str = params.get('range', '7d')

    # Intervall bestimmen
    m = re.match(r"(\d+)([dw])", range_str)
    days = 7
    if m:
        num, unit = int(m.group(1)), m.group(2)
        days = num * (7 if unit == 'w' else 1)

    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=days)

    # Metriken sammeln
    active_users = get_active_users_count(group_id, start_dt, end_dt)
    top_cmds = get_command_usage(group_id, start_dt, end_dt)
    weekday_activity = get_activity_by_weekday(group_id, start_dt, end_dt)
    top_groups = get_top_groups(start_dt, end_dt)

    # Nachricht aufbauen
    text = (
        f"📊 *Statistiken für Gruppe {group_id}*\n"
        f"🗓 Zeitraum: {start_dt.date()} bis {end_dt.date()}\n"
        f"• Aktive Nutzer: `{active_users}`\n"
        "• Top Befehle:\n"
    )
    for cmd, cnt in top_cmds:
        text += f"   – `{cmd}`: {cnt}\n"
    text += "• Aktivität pro Wochentag (0=So):\n"
    for wd, total in weekday_activity:
        text += f"   – {int(wd)}: {int(total)} Nachrichten\n"
    text += "• Top 5 Gruppen (Nachrichten gesamt):\n"
    for cid, total in top_groups:
        text += f"   – {cid}: {int(total)} Nachrichten\n"

    await update.message.reply_text(text, parse_mode='Markdown')

# --- Registrierung der Handler ---
def register_statistics_handlers(app):
    # Schema-Migration initial ausführen
    init_stats_db()
    # Bindet Stats-Command
    app.add_handler(CommandHandler(['stats', 'statistik'], stats_command), group=10)
    # Befehls-Logger für alle anderen Commands
    async def command_logger(update: Update, context: ContextTypes.DEFAULT_TYPE):
        cmd = update.message.text.split()[0].lstrip('/')
        log_command(update.effective_chat.id, update.effective_user.id, cmd)

    app.add_handler(MessageHandler(filters.COMMAND & ~filters.Regex(r'^/stats'), command_logger), group=9)
