import re
import os
import logging
import csv
import json
from psycopg2.extras import Json
from openai import OpenAI
from collections import Counter
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from telegram import Update, Message, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ChatMemberHandler, PollAnswerHandler
from .telethon_client import telethon_client
from telethon.tl.functions.channels import GetFullChannelRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bots.content.database import (_with_cursor, _db_pool, record_reply_time, get_group_language, migrate_stats_rollup, compute_agg_group_day, 
upsert_agg_group_day, get_agg_summary, get_heatmap, get_agg_rows, get_group_stats, get_top_responders
)
from .translator import translate_hybrid


logger = logging.getLogger(__name__)

# OpenAI-Client initialisieren (oder None, wenn kein Key gesetzt)
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
if OPENAI_KEY:
    openai_client = OpenAI(api_key=OPENAI_KEY)
else:
    openai_client = None
    print("[Warnung] OPENAI_API_KEY nicht gesetzt – Sentiment/Summary deaktiviert.")

# Hilfsfunktion für rohe DB-Verbindung
def get_db_connection():
    conn = _db_pool.getconn()
    conn.autocommit = True
    return conn

def get_cursor():
    conn = _db_pool.getconn()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        _db_pool.putconn(conn)

# Deine Developer-IDs für globale Metriken
raw = os.getenv("DEVELOPER_CHAT_IDS", "")
# IDs der Entwickler (DEVELOPER_CHAT_IDS oder DEVELOPER_CHAT_ID)
raw_ids = os.getenv("DEVELOPER_CHAT_IDS") or os.getenv("DEVELOPER_CHAT_ID", "")
DEVELOPER_IDS = {
    int(x) for x in re.split(r"\s*,\s*", raw_ids) 
    if x and x.isdigit()
}
if not DEVELOPER_IDS:
    print("[Warnung] Keine Developer-IDs definiert – /dashboard bleibt gesperrt.")

# --- Telethon-Daten abrufen und speichern ---
async def fetch_and_store_stats(chat_username: str):
    """Fragt via Telethon ab und speichert Mitglieder+Admins in daily_stats."""
    full = await telethon_client(GetFullChannelRequest(chat_username))
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
                datetime.utcnow().date(),
                full.full_chat.participants_count,
                len(full.full_chat.admin_rights or [])
            )
        )
    _db_pool.putconn(conn)

# Scheduler für nächtliche Abfragen
def schedule_telethon_jobs(chat_usernames: list[str]):
    scheduler = AsyncIOScheduler()
    for username in chat_usernames:
        scheduler.add_job(lambda u=username: fetch_and_store_stats(u), trigger='cron', hour=2, minute=0)
    scheduler.start()

# --- Schema-Migration für Stats ---
@_with_cursor
def init_stats_db(cur):

    # 0) Erst die Basistabelle anlegen, falls noch nicht da
    cur.execute("""
        CREATE TABLE IF NOT EXISTS group_settings (
            chat_id   BIGINT PRIMARY KEY,
            title     TEXT,
            description TEXT
        );
    """)

    # 1) Alte group_settings-Spalten auf die neuen Dev-Dashboard-Felder erweitern

    cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS title TEXT;")
    cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS description TEXT;")
    cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS last_command TEXT;")
    cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS last_active TIMESTAMPTZ;")
    cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS group_activity_score REAL DEFAULT 0;")
    cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS topic_count INT DEFAULT 0;")
    cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS bot_count INT DEFAULT 0;")
    cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS member_count INT DEFAULT 0;")
    cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS admin_count INT DEFAULT 0;")

    # 2) Bisherige Statistik-Tabellen
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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS member_events (
            group_id BIGINT,
            user_id  BIGINT,
            event    TEXT,
            event_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_member_events_group ON member_events(group_id);")
    
    # 3) Message-Logging für Dev-Dashboard
    # Erstelle oder passe message_logs an, damit sowohl chat_id als auch group_id unterstützt werden.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS message_logs (
            chat_id     BIGINT,
            message_id  BIGINT,
            user_id     BIGINT,
            content     TEXT,
            is_photo    BOOLEAN DEFAULT FALSE,
            is_video    BOOLEAN DEFAULT FALSE,
            is_sticker  BOOLEAN DEFAULT FALSE,
            is_voice    BOOLEAN DEFAULT FALSE,
            is_location BOOLEAN DEFAULT FALSE,
            is_reply    BOOLEAN DEFAULT FALSE,
            timestamp   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Stelle sicher, dass die Spalte timestamp existiert (für alte Tabellen ohne timestamp)
    cur.execute(
        "ALTER TABLE message_logs ADD COLUMN IF NOT EXISTS timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;"
    )
    # Spalte group_id ergänzen, falls nicht vorhanden
    cur.execute(
        "ALTER TABLE message_logs ADD COLUMN IF NOT EXISTS group_id BIGINT;"
    )
    # Flags hinzufügen, falls nicht vorhanden
    for col in ['is_photo', 'is_video', 'is_sticker', 'is_voice', 'is_location', 'is_reply']:
        cur.execute(f"ALTER TABLE message_logs ADD COLUMN IF NOT EXISTS {col} BOOLEAN DEFAULT FALSE;")
    # Vorhandene chat_id-Werte in group_id kopieren
    cur.execute(
        "UPDATE message_logs SET group_id = chat_id WHERE group_id IS NULL;"
    )
    # Spalte last_message_time ergänzen, sofern nötig (für Dev-Dashboard-Inaktive Benutzer)
    cur.execute(
        "ALTER TABLE message_logs ADD COLUMN IF NOT EXISTS last_message_time TIMESTAMPTZ;"
    )
    # Werte initialisieren: last_message_time = timestamp
    cur.execute(
        "UPDATE message_logs SET last_message_time = timestamp WHERE last_message_time IS NULL;"
    )
    # Index auf group_id anlegen
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_logs_group ON message_logs(group_id);"
    )

    # 4) Poll-Responses speichern für Insights (unverändert) speichern für Insights (unverändert) speichern für Insights (unverändert)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS poll_responses (
            group_id      BIGINT,
            user_id       BIGINT,
            poll_id       BIGINT,
            response_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_poll_responses_group ON poll_responses(group_id);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS spam_events (
          chat_id    BIGINT,
          user_id    BIGINT,
          rule       TEXT,      -- 'link'|'emoji_per_msg'|'flood'|...
          action     TEXT,      -- 'delete'|'mute'|'ban'|'warn'
          details    JSONB,
          ts         TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_spam_events_chat_ts ON spam_events(chat_id, ts DESC);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS night_events (
          chat_id    BIGINT,
          kind       TEXT,      -- 'delete'|'warn'|'hard_on'|'hard_off'|'quietnow'
          count      INT DEFAULT 1,
          until_ts   TIMESTAMPTZ NULL,
          ts         TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_night_events_chat_ts ON night_events(chat_id, ts DESC);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS feature_interactions (
          chat_id  BIGINT,
          user_id  BIGINT,
          feature  TEXT,      -- 'menu:night', 'menu:linksperre', 'faq:thumbs_up', ...
          meta     JSONB,
          ts       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_feature_interactions_chat_ts ON feature_interactions(chat_id, ts DESC);")

    # --- Auto-Responses (Helpful/Answer-ID) für FAQ/Assist auswertbar machen ---
    cur.execute("ALTER TABLE auto_responses ADD COLUMN IF NOT EXISTS was_helpful BOOLEAN;")
    cur.execute("ALTER TABLE auto_responses ADD COLUMN IF NOT EXISTS answer_msg_id BIGINT;")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_auto_responses_chat_ts ON auto_responses(chat_id, ts DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_auto_responses_trigger ON auto_responses(chat_id, trigger);")

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
def get_all_group_ids(cur):
    cur.execute("SELECT chat_id FROM group_settings")
    return [row[0] for row in cur.fetchall()]

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
def get_command_logs(cur, chat_id: int, start_date: datetime, end_date: datetime):
    cur.execute(
        "SELECT user_id, command, used_at FROM command_logs "
        "WHERE chat_id = %s AND used_at BETWEEN %s AND %s "
        "ORDER BY used_at DESC LIMIT 100;",
        (chat_id, start_date, end_date)
    )
    return [{"user_id": u, "command": c, "timestamp": t.isoformat()} 
            for u, c, t in cur.fetchall()]

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

@_with_cursor
def log_message(cur, chat_id: int, msg):
    text = msg.text or msg.caption or None
    topic_id = getattr(msg, "message_thread_id", None)  # ← neu

    cur.execute(
        """
        INSERT INTO message_logs
          (chat_id, group_id, topic_id, message_id, user_id, content,
           is_photo, is_video, is_sticker, is_voice,
           is_location, is_reply, timestamp, last_message_time)
        VALUES
          (%s, %s, %s, %s, %s, %s,
           %s, %s, %s, %s,
           %s, %s, NOW(), NOW())
        ON CONFLICT DO NOTHING;
        """,
        (
            msg.chat.id,                 # chat_id
            msg.chat.id,                 # group_id (Legacy-Feld)
            topic_id,                    # ← neu
            msg.message_id,
            (msg.from_user.id if msg.from_user else None),
            text,
            bool(msg.photo),
            bool(msg.video),
            bool(msg.sticker),
            bool(msg.voice),
            bool(getattr(msg, "location", None)),
            bool(msg.reply_to_message),
        )
    )

@_with_cursor
def log_member_event(cur, group_id: int, user_id: int, event: str):
    cur.execute(
        """
        INSERT INTO member_events (group_id, user_id, event)
        VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING;
        """,
        (group_id, user_id, event)
    )

@_with_cursor
def log_poll_response(cur, chat_id: int, user_id: int, poll_id: int):
    cur.execute(
        """
        INSERT INTO poll_responses (group_id, user_id, poll_id, response_time)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT DO NOTHING;
        """,
        (chat_id, user_id, poll_id)
    )

@_with_cursor
def log_spam_event(cur, chat_id: int, user_id: int, rule: str, action: str, details: dict | None = None):
    cur.execute("""
        INSERT INTO spam_events (chat_id, user_id, rule, action, details)
        VALUES (%s, %s, %s, %s, %s);
    """, (chat_id, user_id, rule, action, Json(details, dumps=json.dumps) if details is not None else None))

@_with_cursor
def log_night_event(cur, chat_id: int, kind: str, count: int = 1, until_ts = None):
    cur.execute("""
        INSERT INTO night_events (chat_id, kind, count, until_ts)
        VALUES (%s, %s, %s, %s);
    """, (chat_id, kind, count, until_ts))

@_with_cursor
def log_feature_interaction(cur, chat_id: int, user_id: int, feature: str, meta: dict | None = None):
    cur.execute("""
        INSERT INTO feature_interactions (chat_id, user_id, feature, meta)
        VALUES (%s, %s, %s, %s);
    """, (chat_id, user_id, feature, Json(meta, dumps=json.dumps) if meta is not None else None))

@_with_cursor
def get_ai_mod_logs_range(cur, chat_id:int, d0, d1):
    cur.execute("""
      SELECT ts, user_id, topic_id, category, score, action
        FROM ai_mod_logs
       WHERE chat_id=%s AND DATE(ts) BETWEEN %s AND %s
       ORDER BY ts ASC;
    """, (chat_id, d0, d1))
    return cur.fetchall() or []

@_with_cursor
def get_user_strikes_snapshot(cur, chat_id:int):
    cur.execute("SELECT user_id, points, updated FROM user_strikes WHERE chat_id=%s ORDER BY points DESC;", (chat_id,))
    return cur.fetchall() or []

def _safe_user_id(m) -> int | None:
    u = getattr(m, "from_user", None)
    return u.id if u else None

def _safe_msg_id(m) -> int | None:
    return getattr(m, "message_id", None)

def _range_for_key(key:str, tz:str):
    now = datetime.now(ZoneInfo(tz))
    today = now.date()
    if key == "today": return today, today
    elif key == "yesterday": return today - timedelta(days=1), today - timedelta(days=1)
    elif key == "7d": return today - timedelta(days=6), today
    elif key == "14d": return today - timedelta(days=13), today
    elif key == "30d": return today - timedelta(days=29), today
    elif key == "60d": return today - timedelta(days=59), today
    return today - timedelta(days=6), today

def _stats_keyboard(cid:int, sel:str, lang:str):
    def btn(label, key):
        mark = "●" if key==sel else "○"
        return InlineKeyboardButton(f"{mark} {label}", callback_data=f"{cid}_stats_range_{key}")
    
    return InlineKeyboardMarkup([
        [btn("Heute", "today"), btn("Gestern", "yesterday")],
        [btn("7 Tage", "7d"), btn("14 Tage", "14d")],
        [btn("30 Tage", "30d"), btn("60 Tage", "60d")],
        [InlineKeyboardButton("🔄 Aktualisieren", callback_data=f"{cid}_stats_refresh")],
        [InlineKeyboardButton("↩️ Zurück", callback_data=f"group_{cid}")]
    ])

def _format_ms(ms:int|None):
    if ms is None: return "–"
    s = ms/1000
    if s < 60: return f"{s:.1f}s"
    m = int(s//60); r = int(s%60)
    return f"{m}m {r}s"

def _render_heatmap_ascii(grid:list[list[int]]):
    # einfache 5-Pegel-Darstellung mit Blöcken ░▄█ (kompakt)
    # Dow: 0..6 = Mo..So, Stunden 0..23
    # Normalisieren pro Gesamtmax
    flat = [c for row in grid for c in row]
    mx = max(flat) if flat else 0
    steps = [' ', '░', '▒', '▓', '█']
    def cell(v):
        if mx<=0: return ' '
        q = v*4//mx
        return steps[q]
    # Kopfzeile (Stunden)
    head = "    " + "".join(f"{h:02d}"[-1] for h in range(24))
    lines = [head]
    wd = ["Mo","Di","Mi","Do","Fr","Sa","So"]
    for i, row in enumerate(grid):
        lines.append(f"{wd[i]} |" + "".join(cell(v) for v in row))
    return "```\n" + "\n".join(lines) + "\n```"

# Neue Version: schreibt in database.reply_times
def log_reply_time(group_id: int, question_msg, answer_msg):
    q_mid = _safe_msg_id(question_msg)
    a_mid = _safe_msg_id(answer_msg)
    q_uid = _safe_user_id(question_msg)
    a_uid = _safe_user_id(answer_msg)
    if not (q_mid and a_mid and q_uid and a_uid):
        return
    delta_ms = int((answer_msg.date - question_msg.date).total_seconds() * 1000)
    record_reply_time(group_id, q_mid, q_uid, a_mid, a_uid, delta_ms)

async def reply_time_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.effective_message
    if not msg or not msg.reply_to_message:
        return
    # Nur sinnvolle Replies (kein Bot-eigener Echo etc.). Optional: Admin-Check hier.
    try:
        chat_id = msg.chat.id
        orig = msg.reply_to_message
        # erste Antwort? – Optional: per Cache/Redis prüfen. Minimal: wir loggen jeden Reply.
        log_reply_time(chat_id, orig, msg)
    except Exception as e:
        logger.warning(f"reply_time_handler Fehler: {e}")

async def _resolve_user_name(bot, chat_id: int, user_id: int) -> str:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        first = member.user.first_name or ""
        last  = member.user.last_name or ""
        name  = (first + " " + last).strip() or f"User {user_id}"
        return f"<a href='tg://user?id={user_id}'>{name}</a>"
    except Exception:
        return f"User {user_id}"

async def poll_response_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Loggt Poll-Antworten, wenn Nutzer abstimmen."""
    answer = update.poll_answer
    poll_id = answer.poll_id
    user_id = answer.user.id
    # Wir brauchen den chat_id, in dem die Umfrage lief.
    # Beim Erstellen der Umfrage speichern wir ihn in bot_data:
    chat_id = context.application.bot_data.get('poll_chat_ids', {}).get(poll_id)
    if chat_id:
        log_poll_response(context.bot, chat_id, user_id, poll_id)

async def send_my_poll(update, context):
    msg = await update.effective_chat.send_poll(
        "Frage?", ["A", "B"], is_anonymous=False
    )
    poll_id = msg.poll.id
    # Speichere chat_id → poll_id
    context.application.bot_data.setdefault('poll_chat_ids', {})[poll_id] = msg.chat.id

async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cm: ChatMemberUpdated = update.chat_member
    # Alte und neue Rolle vergleichen
    old = cm.old_chat_member.status
    new = cm.new_chat_member.status
    if old in ("left", "kicked") and new in ("member", "administrator"):
        event = "join"
    elif old in ("member", "administrator") and new in ("left", "kicked"):
        event = "leave"
    else:
        return  # andere Status-Änderungen ignorieren

    chat_id = cm.chat.id
    user_id = cm.new_chat_member.user.id
    log_member_event(context.bot, chat_id, user_id, event)

async def universal_logger(update, context):
    msg = update.effective_message
    if msg:
        log_message(msg.chat.id, msg)
        
async def fetch_message_stats(chat_id: int, days: int = 7):
    if telethon_client is None:
        # keine Telethon-Stats möglich → leere Struktur zurückgeben
        return {
            "total": 0,
            "by_user": Counter(),
            "by_type": Counter(),
            "by_hour": Counter(),
            "hashtags": Counter(),
        }
    
    # Verbindung prüfen und ggf. herstellen
    if not telethon_client.is_connected():
        await telethon_client.connect()
    since = datetime.utcnow() - timedelta(days=days)
    stats = {
        "total": 0,
        "by_user": Counter(),
        "by_type": Counter(),
        "by_hour": Counter(),
        "hashtags": Counter(),
        "by_weekday_hour": {d: Counter() for d in range(7)}  # NEU
    }

    async for msg in telethon_client.iter_messages(chat_id, offset_date=since):
        stats["total"] += 1
        # Nutzer
        if msg.from_id:
            uid = msg.from_id.user_id or msg.from_id.chat_id
            stats["by_user"][uid] += 1

        # Typ bestimmen mit sicherem Attributzugriff (MessageService hat z.B. kein .location)
        if getattr(msg, "text", None):
            kind = "text"
        elif getattr(msg, "photo", None):
            kind = "photo"
        elif getattr(msg, "video", None):
            kind = "video"
        elif getattr(msg, "sticker", None):
            kind = "sticker"
        elif getattr(msg, "voice", None):
            kind = "voice"
        elif getattr(msg, "location", None):
            kind = "location"
        else:
            kind = "other"
        stats["by_type"][kind] += 1

        # Stunde (nur, wenn date-Attribut vorhanden)
        msg_date = getattr(msg, "date", None)
        if msg_date:
            stats["by_hour"][msg_date.hour] += 1
            try:
                wd = msg_date.weekday()  # 0=Mo
                stats["by_weekday_hour"][wd][msg_date.hour] += 1
            except Exception:
                pass

        # Hashtags
        if msg.text:
            for tag in re.findall(r"#\w+", msg.text):
                stats["hashtags"][tag.lower()] += 1

    return stats

def rolling_window_trend(data: list[int], window: int = 7) -> list[float]:
    """Gleitender Durchschnitt über das Zeitfenster."""
    if len(data) < window:
        return []
    return [
        sum(data[i-window:i]) / window
        for i in range(window, len(data)+1)
    ]

def heatmap_matrix(by_hour: Counter, days: int = 7):
    """
    Erstellt eine matrix [Wochentag][Stunde] mit Nachrichtenzahlen.
    Beispiel-Rückgabe: dict{0: Counter({0:5,1:2,...}), ..., 6: Counter(...)}
    """
    matrix = {d: Counter() for d in range(7)}
    # befüllen: in fetch_message_stats pro msg zusätzlich matrix[msg.date.weekday()][msg.date.hour] += 1
    return matrix

async def compute_response_times(chat_id: int, days: int = 7):
    
    """
    Misst Zeitdifferenz zwischen jeder Erstnachricht und erster Antwort im Thread.
    Gibt Durchschnitt und Median zurück.
    """
    from statistics import mean, median

    if not telethon_client.is_connected():
        await telethon_client.connect()
    since = datetime.utcnow() - timedelta(days=days)
    diffs = []

    # einfacher Ansatz: jede Nachricht, die reply_to_message hat
    async for msg in telethon_client.iter_messages(chat_id, offset_date=since):
        # Nur Antworten betrachten
        if not getattr(msg, "reply_to_msg_id", None):
            continue

        # Originalnachricht holen (gibt Message oder Liste zurück)
        orig = await telethon_client.get_messages(chat_id, ids=msg.reply_to_msg_id)
        # Normieren: falls Liste, nimm erstes Element
        if isinstance(orig, list):
            orig_msg = orig[0] if orig else None
        else:
            orig_msg = orig

        # Wenn keine Originalnachricht oder kein Datum → überspringen
        if not orig_msg or not getattr(orig_msg, "date", None):
            continue

        # Antwortzeit berechnen
        diffs.append((msg.date - orig_msg.date).total_seconds())

    return {
        "average_response_s": mean(diffs) if diffs else None,
        "median_response_s": median(diffs) if diffs else None,
    }

async def fetch_media_and_poll_stats(chat_id: int, days: int = 7):
    if not telethon_client.is_connected():
        await telethon_client.connect()
    since = datetime.utcnow() - timedelta(days=days)
    media = {"photos": 0, "videos": 0, "voices": 0, "docs": 0, "gifs": 0, "polls": 0}

    async for msg in telethon_client.iter_messages(chat_id, offset_date=since):
        # Polls
        if hasattr(msg, "poll") and msg.poll is not None:
            media["polls"] += 1
        # Fotos
        if msg.photo:
            media["photos"] += 1
        # Videos
        if msg.video:
            media["videos"] += 1
        # Voice
        if msg.voice:
            media["voices"] += 1
        # Dokumente (inkl. GIFs)
        if msg.document:
            # GIFs erkennt man z.B. an msg.document.mime_type
            mt = getattr(msg.document, "mime_type", "")
            if "gif" in mt:
                media["gifs"] += 1
            else:
                media["docs"] += 1

    return media

async def analyze_sentiment(texts: list[str]):
    """
    Rückgabe: {'positive': x, 'neutral': y, 'negative': z}
    """
    if not openai_client:
        return "⚠️ Sentiment nicht verfügbar"
    
    prompt = (
        "Analysiere die folgenden Texte und gib pro Text ‚positiv‘, ‚neutral‘ "
        "oder ‚negativ‘ aus:\n\n" + "\n\n".join(texts)
    )
    resp = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0
    )
    # je nach neuer API evtl. resp.choices[0].message.content oder resp.choices[0].message…
    return resp.choices[0].message.content

async def summarize_conversation(chat_id: int, days: int = 1):
    """
    Holt die letzten Chat-Nachrichten und fasst sie in bis zu 5 Sätzen zusammen.
    """
    # Guard: OpenAI-Client prüfen
    if not openai_client:
        return "⚠️ Zusammenfassung nicht verfügbar (kein API-Key)."

    # 1) Nachrichten sammeln
    msgs = []
    async for msg in telethon_client.iter_messages(chat_id, limit=50):
        if msg.text:
            msgs.append(msg.text)

    # 2) Prompt bauen
    text_block = "\n\n".join(msgs) if msgs else "<keine Nachrichten>"
    prompt = (
        "Fasse die folgenden Kurznachrichten in maximal 5 Sätzen zusammen:\n\n"
        f"{text_block}"
    )

    # 3) OpenAI-Request mit neuer API
    resp = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    # Je nach Response-Shape:
    return resp.choices[0].message.content

async def export_stats_csv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    bot  = context.bot
    args = context.args or []

    # range= today | yesterday | 7d | 30d  (Default: 7d)
    params = {k: v for a in args if "=" in a for k, v in [a.split("=", 1)]}
    key = params.get("range", "7d")
    tz  = "Europe/Berlin"
    d0, d1 = _range_for_key(key, tz)
    ts_start = datetime.combine(d0, datetime.min.time())
    ts_end   = datetime.combine(d1 + timedelta(days=1), datetime.min.time())

    # --- 1) Tages-Rollups & Top-Responder
    rows = get_agg_rows(chat.id, d0, d1)
    top  = get_top_responders(chat.id, d0, d1, limit=10)

    # --- 2) Command-Usage
    cmd_usage = get_command_usage(chat.id, ts_start, ts_end)

    # --- 3) Engagement (Reply-Rate & Ø-Delay)
    engage = get_engagement_metrics(chat.id, ts_start, ts_end)

    # --- 4) Message-Insights (Medientypen/Polls)
    insights = get_message_insights(chat.id, ts_start, ts_end)

    # --- 5) Aktivität nach Wochentag
    by_weekday = get_activity_by_weekday(chat.id, ts_start, ts_end)  # [(dow, total), ...]

    # --- 6) Stundenverteilung (0..23) + weitere DB-Queries
    #    Spam-Events (rule/action), Night-Events (kind), Member-Events (event), Top-Poster
    from psycopg2.extras import DictCursor
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=DictCursor)

        cur.execute("""
            SELECT EXTRACT(HOUR FROM timestamp)::INT AS hour, COUNT(*) AS cnt
            FROM message_logs
            WHERE group_id=%s AND timestamp BETWEEN %s AND %s
            GROUP BY 1 ORDER BY 1
        """, (chat.id, ts_start, ts_end))
        by_hour = cur.fetchall() or []

        # NEU: Wochentag×Stunde
        cur.execute("""
            SELECT EXTRACT(DOW FROM timestamp)::INT AS dow,
                   EXTRACT(HOUR FROM timestamp)::INT AS hour,
                   COUNT(*) AS cnt
            FROM message_logs
            WHERE group_id=%s AND timestamp BETWEEN %s AND %s
            GROUP BY 1,2
            ORDER BY 1,2
        """, (chat.id, ts_start, ts_end))
        by_wd_hour = cur.fetchall() or []

        cur.execute("""
            SELECT rule, COUNT(*) AS cnt
            FROM spam_events
            WHERE chat_id=%s AND ts BETWEEN %s AND %s
            GROUP BY rule ORDER BY cnt DESC
        """, (chat.id, ts_start, ts_end))
        spam_by_rule = cur.fetchall() or []

        cur.execute("""
            SELECT action, COUNT(*) AS cnt
            FROM spam_events
            WHERE chat_id=%s AND ts BETWEEN %s AND %s
            GROUP BY action ORDER BY cnt DESC
        """, (chat.id, ts_start, ts_end))
        spam_by_action = cur.fetchall() or []

        cur.execute("""
            SELECT kind, COALESCE(SUM(count),0) AS cnt
            FROM night_events
            WHERE chat_id=%s AND ts BETWEEN %s AND %s
            GROUP BY kind ORDER BY cnt DESC
        """, (chat.id, ts_start, ts_end))
        night_by_kind = cur.fetchall() or []

        cur.execute("""
            SELECT event, COUNT(*) AS cnt
            FROM member_events
            WHERE group_id=%s AND event_time BETWEEN %s AND %s
            GROUP BY event ORDER BY cnt DESC
        """, (chat.id, ts_start, ts_end))
        member_by_event = cur.fetchall() or []

        cur.execute("""
            SELECT user_id, COUNT(*) AS msgs
            FROM message_logs
            WHERE group_id=%s AND timestamp BETWEEN %s AND %s
            GROUP BY user_id
            ORDER BY msgs DESC
            LIMIT 20
        """, (chat.id, ts_start, ts_end))
        top_posters = cur.fetchall() or []

        cur.execute("""
            SELECT trigger, COUNT(*) AS hits,
                   SUM(CASE WHEN was_helpful IS TRUE THEN 1 ELSE 0 END) AS helpful
            FROM auto_responses
            WHERE chat_id=%s AND ts BETWEEN %s AND %s
            GROUP BY trigger
            ORDER BY hits DESC
            LIMIT 20
        """, (chat.id, ts_start, ts_end))
        autoresp_by_trigger = cur.fetchall() or []

        # --- NEU: Inaktive Nutzer (14d / 30d) ---
        cur.execute("""
            SELECT user_id, MAX(last_message_time) AS last_seen
            FROM message_logs
            WHERE group_id=%s
            GROUP BY user_id
            HAVING MAX(last_message_time) < NOW() - INTERVAL '14 days'
            ORDER BY last_seen ASC
            LIMIT 200
        """, (chat.id,))
        inactive_14d = cur.fetchall() or []

        cur.execute("""
            SELECT user_id, MAX(last_message_time) AS last_seen
            FROM message_logs
            WHERE group_id=%s
            GROUP BY user_id
            HAVING MAX(last_message_time) < NOW() - INTERVAL '30 days'
            ORDER BY last_seen ASC
            LIMIT 200
        """, (chat.id,))
        inactive_30d = cur.fetchall() or []

    finally:
        _db_pool.putconn(conn)

    # --- CSV schreiben
    fname = f"/tmp/stats_{chat.id}_{d0.isoformat()}_{d1.isoformat()}.csv"
    with open(fname, "w", encoding="utf-8", newline="") as f:
        wr = csv.writer(f, delimiter=";")

        # Kopf: Metadaten
        wr.writerow(["chat_id", "range", "from", "to"])
        wr.writerow([chat.id, key, d0.isoformat(), d1.isoformat()])

        # 1) Tages-Rollups
        wr.writerow([])
        wr.writerow(["# Tages-Rollups (agg_group_day)"])
        wr.writerow([
            "date","messages_total","active_users","joins","leaves","kicks",
            "reply_median_ms","reply_p90_ms","autoresp_hits","autoresp_helpful",
            "spam_actions","night_deletes"
        ])
        for (stat_date, m, au, j, l, k, p50, p90, ar_h, ar_hp, spam, ng) in rows or []:
            wr.writerow([stat_date, m, au, j, l, k, p50, p90, ar_h, ar_hp, spam, ng])

        # 2) Top-Responder
        wr.writerow([])
        wr.writerow(["# Top-Responder (IDs; Namen optional in App anzeigen)"])
        wr.writerow(["user_id","answers","avg_delay_ms"])
        for uid, answers, avg_ms in top:
            wr.writerow([uid, answers, avg_ms])

        # 3) Command-Usage
        wr.writerow([])
        wr.writerow(["# Command-Usage"])
        wr.writerow(["command","count"])
        for cmd, cnt in (cmd_usage or []):
            wr.writerow([cmd, cnt])

        # 4) Engagement
        wr.writerow([])
        wr.writerow(["# Engagement"])
        wr.writerow(["reply_rate_pct","avg_delay_s"])
        wr.writerow([engage.get("reply_rate_pct", 0), engage.get("avg_delay_s")])

        # 5) Message-Insights
        wr.writerow([])
        wr.writerow(["# Message-Insights"])
        wr.writerow(["total","photo","video","sticker","voice","location","polls"])
        wr.writerow([
            insights.get("total",0), insights.get("photo",0), insights.get("video",0),
            insights.get("sticker",0), insights.get("voice",0), insights.get("location",0),
            insights.get("polls",0)
        ])

        # 6) Aktivität nach Wochentag
        wr.writerow([])
        wr.writerow(["# Aktivität nach Wochentag (0=Sonntag, 1=Montag, ... 6=Samstag)"])
        wr.writerow(["weekday","messages"])
        for dow, total in (by_weekday or []):
            wr.writerow([int(dow), int(total or 0)])

        # 7) Stundenverteilung
        wr.writerow([])
        wr.writerow(["# Stundenverteilung (0..23)"])
        wr.writerow(["hour","messages"])
        for row in by_hour:
            wr.writerow([int(row["hour"]), int(row["cnt"])])

        # 8) Spam-Events
        wr.writerow([])
        wr.writerow(["# Spam-Events nach Regel"])
        wr.writerow(["rule","count"])
        for row in spam_by_rule:
            wr.writerow([row["rule"], int(row["cnt"])])

        wr.writerow([])
        wr.writerow(["# Spam-Events nach Aktion"])
        wr.writerow(["action","count"])
        for row in spam_by_action:
            wr.writerow([row["action"], int(row["cnt"])])

        # 9) Nachtmodus-Events
        wr.writerow([])
        wr.writerow(["# Nachtmodus-Events"])
        wr.writerow(["kind","count"])
        for row in night_by_kind:
            wr.writerow([row["kind"], int(row["cnt"])])

        # 10) Member-Events
        wr.writerow([])
        wr.writerow(["# Member-Events"])
        wr.writerow(["event","count"])
        for row in member_by_event:
            wr.writerow([row["event"], int(row["cnt"])])

        # 11) Auto-Responses nach Trigger
        wr.writerow([])
        wr.writerow(["# Auto-Responses pro Trigger"])
        wr.writerow(["trigger","hits","helpful"])
        for row in autoresp_by_trigger:
            wr.writerow([row["trigger"], int(row["hits"]), int(row["helpful"] or 0)])

        # 12) Top-Poster
        wr.writerow([])
        wr.writerow(["# Top-Poster"])
        wr.writerow(["user_id","messages"])
        for row in top_posters:
            wr.writerow([int(row["user_id"]), int(row["msgs"])])

        # NEU: Heatmap (Wochentag×Stunde)
        wr.writerow([])
        wr.writerow(["# Heatmap Wochentag×Stunde (dow; hour; cnt)  (0=So/PG abhängig; bei uns 0=So, 1=Mo ... je nach DB)"])
        for dow, hour, cnt in by_wd_hour:
            wr.writerow([int(dow), int(hour), int(cnt)])

        # --- NEU: Inaktive Nutzer ---
        wr.writerow([])
        wr.writerow(["# Inaktive Nutzer >14 Tage (user_id; last_seen_iso)"])
        for uid, last_seen in inactive_14d:
            wr.writerow([uid, getattr(last_seen, "isoformat", lambda: str(last_seen))()])

        wr.writerow([])
        wr.writerow(["# Inaktive Nutzer >30 Tage (user_id; last_seen_iso)"])
        for uid, last_seen in inactive_30d:
            wr.writerow([uid, getattr(last_seen, "isoformat", lambda: str(last_seen))()])

            # AI Moderation Logs
        rows = get_ai_mod_logs_range(chat.id, d0, d1)
        wr.writerow(["ts","user_id","topic_id","category","score","action"])
        for ts, uid, tid, cat, sc, act in rows:
            wr.writerow([ts, uid, tid, cat, sc, act])

        # User Strikes Snapshot
        rows = get_user_strikes_snapshot(chat.id)
        wr.writerow(["user_id","points","updated"])
        for uid, pts, upd in rows:
            wr.writerow([uid, pts, upd])
        
    await update.effective_message.reply_document(
        open(fname, "rb"),
        filename=f"stats_{chat.id}_{d0}_{d1}.csv"
    )

# --- Stats-Command ---
async def stats_command(update, context):
    chat = update.effective_chat
    lang = get_group_language(chat.id) or 'de'
    # Standard: 7 Tage
    sel = "7d"
    tz = "Europe/Berlin"  # optional aus Gruppensettings
    d0, d1 = _range_for_key(sel, tz)

    # Summen/Kacheln laden
    summary = get_agg_summary(chat.id, d0, d1)
    heat = get_heatmap(chat.id, datetime.combine(d0, datetime.min.time()), datetime.combine(d1+timedelta(days=1), datetime.min.time()))

    text = (
        f"📊 <b>Statistiken</b> ({d0.strftime('%d.%m.%Y')}–{d1.strftime('%d.%m.%Y')})\n"
        f"\n<b>Engagement</b>"
        f"\n• Nachrichten: <b>{summary['messages_total']}</b>"
        f"\n• Aktive Nutzer: <b>{summary['active_users']}</b>"
        f"\n• Joins/Leaves/Kicks: <b>{summary['joins']}/{summary['leaves']}/{summary['kicks']}</b>"
        f"\n\n<b>Antwortzeiten</b>"
        f"\n• Median (p50): <b>{_format_ms(summary['reply_median_ms'])}</b>"
        f"\n• p90: <b>{_format_ms(summary['reply_p90_ms'])}</b>"
        f"\n\n<b>Assist (FAQ/Auto)</b>"
        f"\n• Treffer: <b>{summary['autoresp_hits']}</b>"
        f"\n• Hilfreich: <b>{summary['autoresp_helpful']}</b>"
        f"\n\n<b>Moderation</b>"      
        f"\n• Spam-Aktionen: <b>{summary['spam_actions']}</b>"
        f"\n• Nacht-Löschungen: <b>{summary['night_deletes']}</b>"
        f"\n\n<b>Heatmap (Stunde × Wochentag)</b>\n"
        f"{_render_heatmap_ascii(heat)}"
    )
    # Top-Responder separat anhängen
    top = get_top_responders(chat.id, d0, d1, limit=5)
    top_lines = []
    for uid, answers, avg_ms in top:
        name = await _resolve_user_name(context.bot, chat.id, uid)
        s = avg_ms / 1000
        s_str = f"{int(s//60)}m {int(s%60)}s" if s >= 60 else f"{s:.1f}s"
        top_lines.append(f"• {name}: <b>{answers}</b> Antworten, Ø {s_str}")
    text += ("\n<b>Top-Responder</b>\n" + ("\n".join(top_lines) if top_lines else "–"))

    try:
        if update.message:
            await update.message.reply_text(
                text,
                reply_markup=_stats_keyboard(chat.id, sel, lang),
                parse_mode="HTML",
            )
        else:
            # aus dem Menü (Callback)
            await update.callback_query.edit_message_text(
                text,
                reply_markup=_stats_keyboard(chat.id, sel, lang),
                parse_mode="HTML",
            )
    except Exception as e:
        # Fallback (z. B. "message is not modified" / "message can't be edited")
        await context.bot.send_message(
            chat_id=chat.id,
            text=text,
            reply_markup=_stats_keyboard(chat.id, sel, lang),
            parse_mode="HTML",
        )

async def stats_callback(update, context):
    query = update.callback_query
    data = query.data  # z.B. "123456_stats_range_7d"
    try:
        cid, _, _, key = data.split("_", 3)  # cid_stats_range_KEY
        cid = int(cid)
    except Exception:
        return
    lang = get_group_language(cid) or 'de'
    tz = "Europe/Berlin"
    d0, d1 = _range_for_key(key, tz)
    summary = get_agg_summary(cid, d0, d1)
    heat = get_heatmap(cid, datetime.combine(d0, datetime.min.time()), datetime.combine(d1+timedelta(days=1), datetime.min.time()))
    text = (
        f"📊 <b>Statistiken</b> ({d0.strftime('%d.%m.%Y')}–{d1.strftime('%d.%m.%Y')})\n"
        f"\n<b>Engagement</b>"
        f"\n• Nachrichten: <b>{summary['messages_total']}</b>"
        f"\n• Aktive Nutzer: <b>{summary['active_users']}</b>"
        f"\n• Joins/Leaves/Kicks: <b>{summary['joins']}/{summary['leaves']}/{summary['kicks']}</b>"
        f"\n\n<b>Antwortzeiten</b>"
        f"\n• Median (p50): <b>{_format_ms(summary['reply_median_ms'])}</b>"
        f"\n• p90: <b>{_format_ms(summary['reply_p90_ms'])}</b>"
        f"\n\n<b>Assist (FAQ/Auto)</b>"
        f"\n• Treffer: <b>{summary['autoresp_hits']}</b>"
        f"\n• Hilfreich: <b>{summary['autoresp_helpful']}</b>"
        f"\n\n<b>Moderation</b>"
        f"\n• Spam-Aktionen: <b>{summary['spam_actions']}</b>"
        f"\n• Nacht-Löschungen: <b>{summary['night_deletes']}</b>"
        f"\n\n<b>Heatmap (Stunde × Wochentag)</b>\n"
        f"{_render_heatmap_ascii(heat)}"
    )
    # Top-Responder separat + korrektes cid
    top = get_top_responders(cid, d0, d1, limit=5)
    top_lines = []
    for uid, answers, avg_ms in top:
        name = await _resolve_user_name(context.bot, cid, uid)
        s = avg_ms / 1000
        s_str = f"{int(s//60)}m {int(s%60)}s" if s >= 60 else f"{s:.1f}s"
        top_lines.append(f"• {name}: <b>{answers}</b> Antworten, Ø {s_str}")
    text += ("\n<b>Top-Responder</b>\n" + ("\n".join(top_lines) if top_lines else "–"))

    await query.edit_message_text(text, reply_markup=_stats_keyboard(cid, key, lang), parse_mode="HTML")


async def get_group_meta(chat_id: int) -> dict:
    meta = {
        "title":      "–",
        "description":"–",
        "members":    None,
        "admins":     None,
        "topics":     None,
        "bots":       None
    }

    # 1) Versuch: live über Telethon
    telethon_ok = False
    if telethon_client:
        try:
            entity = await telethon_client.get_entity(chat_id)
            full   = await telethon_client(GetFullChannelRequest(entity.username or entity.id))
            # Robuster Zugriff auf admins und topics:
            admins = getattr(full.full_chat, "admins_count", None)
            if admins is None:
                admins = 0
            topics = getattr(getattr(full.full_chat, "forum_info", None), "total_count", 0) or 0
            bots = len(getattr(full.full_chat, "bot_info", []) or [])
            meta.update({
                "title":       getattr(entity, "title", "–"),
                "description": getattr(full.full_chat, "about", "–"),
                "members":     getattr(full.full_chat, "participants_count", None),
                "bots":        bots,
                "admins":      admins,
                "topics":      topics
            })
            telethon_ok = True
        except Exception as e:
            logger.warning(f"get_group_meta: Telethon-Fallback fehlgeschlagen: {e}")

    # 2) Fallback: aus DB, wenn Telethon nicht erfolgreich oder Felder fehlen
    if not telethon_ok or meta["title"] == "–" or meta["description"] == "–":
        try:
            conn = get_db_connection()
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT title, description, topic_count AS topics, bot_count AS bots
                    FROM group_settings
                    WHERE chat_id=%s
                """, (chat_id,))
                row = cur.fetchone()
                if row:
                    meta.update({
                        "title":       row[0] or meta["title"],
                        "description": row[1] or meta["description"],
                        "topics":      row[2] if meta["topics"] is None else meta["topics"],
                        "bots":        row[3] if meta["bots"] is None else meta["bots"]
                    })
            finally:
                _db_pool.putconn(conn)
        except Exception:
            pass

    return meta

# 2) Neue/Verlassene Mitglieder & Inaktive
def get_member_stats(chat_id: int, since: datetime) -> dict:
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Neue Member
        cur.execute("""
            SELECT COUNT(*) FROM member_events 
             WHERE group_id=%s AND event='join' AND event_time >= %s
        """, (chat_id, since))
        new_count = cur.fetchone()[0]
        # Verlassene Member
        cur.execute("""
            SELECT COUNT(*) FROM member_events 
             WHERE group_id=%s AND event='leave' AND event_time >= %s
        """, (chat_id, since))
        left_count = cur.fetchone()[0]
        # Inaktive (kein Post seit X Tage)
        threshold = since - timedelta(days=7)
        cur.execute("""
            SELECT COUNT(DISTINCT user_id) 
              FROM message_logs 
             WHERE group_id=%s 
               AND last_message_time < %s
        """, (chat_id, threshold))
        inactive = cur.fetchone()[0]
        return {
            "new":      new_count,
            "left":     left_count,
            "inactive": inactive,
        }
    finally:
        _db_pool.putconn(conn)

# 3) Nachrichten-Insights (Medien-, Poll-, Forward-Statistiken)
def get_message_insights(chat_id: int, start: datetime, end: datetime) -> dict:
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Gesamt-Nachrichten
        cur.execute("""
            SELECT COUNT(*) FROM message_logs
             WHERE group_id=%s AND timestamp BETWEEN %s AND %s
        """, (chat_id, start, end))
        total = cur.fetchone()[0]
        # Medienverteilung
        cur.execute("""
            SELECT 
              SUM(CASE WHEN is_photo THEN 1 ELSE 0 END),
              SUM(CASE WHEN is_video THEN 1 ELSE 0 END),
              SUM(CASE WHEN is_sticker THEN 1 ELSE 0 END),
              SUM(CASE WHEN is_voice THEN 1 ELSE 0 END),
              SUM(CASE WHEN is_location THEN 1 ELSE 0 END)
            FROM message_logs
           WHERE group_id=%s AND timestamp BETWEEN %s AND %s
        """, (chat_id, start, end))
        photo, video, sticker, voice, location = cur.fetchone()
        # Poll-Antworten
        cur.execute("""
            SELECT COUNT(*) FROM poll_responses
             WHERE group_id=%s AND response_time BETWEEN %s AND %s
        """, (chat_id, start, end))
        polls = cur.fetchone()[0]
        return {
            "total":    total,
            "photo":    photo,
            "video":    video,
            "sticker":  sticker,
            "voice":    voice,
            "location": location,
            "polls":    polls,
        }
    finally:
        _db_pool.putconn(conn)

# 4) Engagement (Antwort-Rate & Reaktionszeiten)
def get_engagement_metrics(chat_id: int, start: datetime, end: datetime) -> dict:
    from statistics import mean
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Antwort-Rate: replies / total_messages
        cur.execute("""
            SELECT COUNT(*) FROM message_logs
             WHERE group_id=%s AND is_reply AND timestamp BETWEEN %s AND %s
        """, (chat_id, start, end))
        replies = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM message_logs
             WHERE group_id=%s AND timestamp BETWEEN %s AND %s
        """, (chat_id, start, end))
        total = cur.fetchone()[0]
        rate = round((replies/total*100) if total else 0, 1)
        # Reaktionszeiten: hier exemplarisch aus reply_times-Tabelle
        cur.execute("""
            SELECT delta_ms FROM reply_times
             WHERE chat_id=%s AND ts BETWEEN %s AND %s
        """, (chat_id, start, end))
        delays_ms = [r[0] for r in cur.fetchall()]
        return {
            "reply_rate_pct": rate,
            "avg_delay_s":    round(mean([d/1000 for d in delays_ms]), 1) if delays_ms else None,
        }
    finally:
        _db_pool.putconn(conn)

# 5) Trend-Analyse (Verlauf über Wochen/Monate)
def get_trend_analysis(chat_id: int, periods: int = 4) -> dict:
    today = datetime.utcnow().date()
    results = []
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        for w in range(periods):
            end = today - timedelta(weeks=w)
            start = end - timedelta(weeks=1)
            cur.execute("""
                SELECT COUNT(*) FROM message_logs
                 WHERE group_id=%s AND timestamp::date BETWEEN %s AND %s
            """, (chat_id, start, end))
            results.append((str(start), cur.fetchone()[0]))
        return dict(results)
    finally:
        _db_pool.putconn(conn)

def update_group_activity_score(chat_id: int, score: float):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE group_settings SET group_activity_score=%s WHERE chat_id=%s;",
            (score, chat_id)
        )
        conn.commit()
    finally:
        _db_pool.putconn(conn)

# --- Handler-Registrierung ---
def register_statistics_handlers(app):
    init_stats_db()
    app.add_handler(CommandHandler(['stats', 'statistik'], stats_command), group=10)
    async def command_logger(update: Update, context: ContextTypes.DEFAULT_TYPE):
        cmd = update.effective_message.text.split()[0].lstrip('/')
        log_command(update.effective_chat.id, update.effective_user.id, cmd)
    app.add_handler(MessageHandler(filters.COMMAND & ~filters.Regex(r'^/stats'), command_logger), group=9)
    app.add_handler(MessageHandler(filters.ALL, universal_logger), group=5)
    app.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.CHAT_MEMBER), group=0)
    app.add_handler(MessageHandler(filters.REPLY, reply_time_handler), group=0)
    app.add_handler(PollAnswerHandler(poll_response_handler), group=0)
    app.add_handler(CallbackQueryHandler(stats_callback, pattern=r"^\d+_stats_(?:range|refresh|export|heat).*"), group=0)


