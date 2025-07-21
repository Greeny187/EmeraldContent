import re
import os
import logging
import psycopg2
from openai import OpenAI
from collections import Counter
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from telethon_client import telethon_client
from telethon.tl.functions.channels import GetFullChannelRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database import _with_cursor, _db_pool

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
     # 1) Alte group_settings-Spalten auf die neuen Dev-Dashboard-Felder erweitern
    cur.execute("""
    ALTER TABLE group_settings
      ADD COLUMN IF NOT EXISTS last_command TEXT,
      ADD COLUMN IF NOT EXISTS last_active TIMESTAMPTZ,
      ADD COLUMN IF NOT EXISTS group_activity_score REAL DEFAULT 0,
      ADD COLUMN IF NOT EXISTS description TEXT,
      ADD COLUMN IF NOT EXISTS topic_count INT DEFAULT 0,
      ADD COLUMN IF NOT EXISTS bot_count INT DEFAULT 0;
    """
    )

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
    # Bestehende Tabelle anpassen oder neu erstellen
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS message_logs (
            chat_id    BIGINT,
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
    # Füge group_id hinzu, falls Tabelle schon existierte mit chat_id
    cur.execute(
        "ALTER TABLE message_logs ADD COLUMN IF NOT EXISTS group_id BIGINT;"
    )
    # Kopiere vorhandene chat_id-Werte
    cur.execute(
        "UPDATE message_logs SET group_id = chat_id WHERE group_id IS NULL;"
    )
    # Index auf group_id
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_logs_group ON message_logs(group_id);"
    )

    # 4) Poll-Responses speichern für Insights
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

    # 5) Reply-Zeiten für Engagement-Metriken
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reply_times (
            group_id        BIGINT,
            response_delay_s REAL,
            replied_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reply_times_group ON reply_times(group_id);")

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
    }

    async for msg in telethon_client.iter_messages(chat_id, offset_date=since):
        stats["total"] += 1
        # Nutzer
        if msg.from_id:
            uid = msg.from_id.user_id or msg.from_id.chat_id
            stats["by_user"][uid] += 1

        # Typ
        kind = ("text" if msg.text else
                "photo" if msg.photo else
                "video" if msg.video else
                "sticker" if msg.sticker else
                "voice" if msg.voice else
                "location" if msg.location else
                "other")
        stats["by_type"][kind] += 1

        # Stunde
        stats["by_hour"][msg.date.hour] += 1

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
        if msg.reply_to_msg_id:
            orig = await telethon_client.get_messages(chat_id, ids=msg.reply_to_msg_id)
            diff = (msg.date - orig.date).total_seconds()
            diffs.append(diff)

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
    """Exportiere alle relevanten Metriken als CSV und sende die Datei."""
    chat_id = context.user_data.get("stats_group_id") or update.effective_chat.id
    # Beispiel: hole Basis-Stats
    active = get_active_users_count(chat_id, datetime.utcnow()-timedelta(days=7), datetime.utcnow())
    cmds   = get_command_usage(chat_id, datetime.utcnow()-timedelta(days=7), datetime.utcnow())
    # CSV schreiben
    fname = f"/tmp/stats_{chat_id}.csv"
    with open(fname, "w", encoding="utf-8") as f:
        f.write("Metrik;Wert\n")
        f.write(f"aktive_nutzer;{active}\n")
        for cmd, cnt in cmds:
            f.write(f"cmd_{cmd};{cnt}\n")
    await update.effective_message.reply_document(open(fname, "rb"))

async def stats_dev_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dev-Dashboard: ausführliche Statistiken für Developer."""
    user_id = update.effective_user.id
    if user_id not in DEVELOPER_IDS:
        return await update.effective_message.reply_text("❌ Zugriff verweigert.")

    chat_id = context.user_data.get("stats_group_id") or update.effective_chat.id
    end     = datetime.utcnow()
    start   = end - timedelta(days=7)

    # 1) Basis-Daten
    meta     = get_group_meta(chat_id)
    members  = get_member_stats(chat_id, start)
    insights = get_message_insights(chat_id, start, end)
    engage   = get_engagement_metrics(chat_id, start, end)
    trends   = get_trend_analysis(chat_id, periods=4)

    # 2) Ausgabe formatieren
    meta = await get_group_meta(chat_id)
    text = (
        f"*Gruppe:* {meta['title']} (`{chat_id}`)\n"
        f"📝 Beschreibung: {meta['description']}\n"
        f"👥 Mitglieder: {meta['members']}  👮 Admins: {meta['admins']}\n"
        f"📂 Topics: {meta['topics']}\n\n"
        f"*Dev-Dashboard Gruppe {chat_id} (letzte 7 Tage)*\n\n"
        f"📝 Beschreibung: {meta['description']}\n"
        f"🔖 Topics: {meta['topics']}  🤖 Bots: {meta['bots']}\n\n"
        f"👥 Neue Member: {members['new']}  👋 Left: {members['left']}  💤 Inaktiv: {members['inactive']}\n\n"
        f"💬 Nachrichten gesamt: {insights['total']}\n"
        f"   • Fotos: {insights['photo']}  Videos: {insights['video']}  Sticker: {insights['sticker']}\n"
        f"   • Voice: {insights['voice']}  Location: {insights['location']}  Polls: {insights['polls']}\n\n"
        f"⏱️ Antwort-Rate: {engage['reply_rate_pct']} %  Ø-Delay: {engage['avg_delay_s']} s\n\n"
        "📈 Trend (Woche → Nachrichten):\n"
    )
    for week_start, count in trends.items():
        text += f"   – {week_start}: {count}\n"
    await update.effective_message.reply_text(text, parse_mode="Markdown")

# --- Stats-Command ---
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Guards
    if not openai_client:
        print("[Info] Sentiment/Summary-API fehlt, überspringe OpenAI-Aufrufe.")
    
    chat_id = update.effective_chat.id
    days = int(context.args[0][:-1]) if context.args and context.args[0].endswith("d") else 7

    # 1) Basis-Stats bleiben wie gehabt …
    msg_stats   = await fetch_message_stats(chat_id, days)
    resp_times  = await compute_response_times(chat_id, days)
    media_stats = await fetch_media_and_poll_stats(chat_id, days)

    # 2) Sentiment-Analyse: echte Texte statt Platzhalter
    texts: list[str] = []
    # lade bis zu 10 letzte Nachrichten aus dem Zeitraum
    since = datetime.utcnow() - timedelta(days=days)
    async for msg in telethon_client.iter_messages(chat_id, offset_date=since, limit=10):
        if msg.text:
            texts.append(msg.text)

    if texts:
        sentiment = await analyze_sentiment(texts)
    else:
        sentiment = "Keine Nachrichten zum Analysieren"

    # 3) Strings für Ø/Med wie zuvor mit Fallback
    avg = resp_times.get('average_response_s')
    med = resp_times.get('median_response_s')
    avg_str = f"{avg:.1f}s" if avg is not None else "Keine Daten verfügbar"
    med_str = f"{med:.1f}s" if med is not None else "Keine Daten verfügbar"

    # 4) Ausgabe zusammenbauen (nur einmal, ohne doppelte text_lines)
    output = [
        f"*Letzte {days} Tage:*",
        f"• Nachrichten gesamt: {msg_stats['total']}",
        f"• Top 3 Absender: " + ", ".join(str(u) for u,_ in msg_stats["by_user"].most_common(3)),
        f"• Reaktionszeit Ø/Med: {avg_str} / {med_str}",
        f"• Medien: " + ", ".join(f"{k}={v}" for k,v in media_stats.items()),
        f"• Stimmung (GPT): {sentiment}"
    ]
    await update.effective_message.reply_text("\n".join(output), parse_mode="Markdown")

    # 1) Prüfen, ob Menü-Callback die Gruppe vorgibt
    group_id = context.user_data.pop("stats_group_id", None)
    # 2) Fallback auf /stats-Argument oder aktuelle Chat-ID
    args = context.args or []
    params = {k: v for arg in args if "=" in arg for k, v in [arg.split("=",1)]}
    if group_id is None:
        group_id = int(params.get("group", update.effective_chat.id))
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

async def get_group_meta(chat_id: int) -> dict:
    """
    Liefert Title, Beschreibung, Anzahl Mitglieder/Admins und Topics:
    – per Telethon, falls konfiguriert
    – fallback: Platzhalter, falls DB-Spalten fehlen
    """
    meta = {
        "title":      "–",
        "description":"–",
        "members":    None,
        "admins":     None,
        "topics":     None,
    }

        # 1) Versuch: live über Telethon
    if telethon_client:
        try:
            # Chat-Entity holen (Username oder ID)
            entity = await telethon_client.get_entity(chat_id)
            full   = await telethon_client(GetFullChannelRequest(entity.username or entity.id))
            meta.update({
                "title":      getattr(entity, "title", "–"),
                "description": getattr(full.full_chat, "about", "–"),
                "members":     full.full_chat.participants_count,
                "admins":      len(full.full_chat.admin_rights or []),
                # forum_info.total_count nur bei supergruppen mit Topics
                "topics":      getattr(full.full_chat, "forum_info", {}).get("total_count", 0)
            })
        except Exception as e:
            logger.warning(f"get_group_meta: Telethon-Fallback fehlgeschlagen: {e}")

    # 2) Versuch: ggf. aus DB-Spalten (falls Schema angepasst wurde)
    else:
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT title, description, topic_count, bot_count
                    FROM group_settings
                    WHERE group_id=%s
                """, (chat_id,))
                row = cur.fetchone()
            if row:
                meta.update({
                    "title":       row[0],
                    "description": row[1],
                    "topics":      row[2],
                    # bot_count gibt nur Anzahl registrierter Bot-Instanzen in der Tabelle wieder
                    "bots":        row[3]
                })
        except Exception:
            # Spalten existieren nicht – ignorieren
            pass

    return meta

# 2) Neue/Verlassene Mitglieder & Inaktive
def get_member_stats(chat_id: int, since: datetime) -> dict:
    with get_db_connection() as conn:
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

# 3) Nachrichten-Insights (Medien-, Poll-, Forward-Statistiken)
def get_message_insights(chat_id: int, start: datetime, end: datetime) -> dict:
    with get_db_connection() as conn:
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

# 4) Engagement (Antwort-Rate & Reaktionszeiten)
def get_engagement_metrics(chat_id: int, start: datetime, end: datetime) -> dict:
    from statistics import mean
    with get_db_connection() as conn:
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
            SELECT response_delay_s FROM reply_times
             WHERE group_id=%s AND replied_at BETWEEN %s AND %s
        """, (chat_id, start, end))
        delays = [r[0] for r in cur.fetchall()]
    return {
        "reply_rate_pct": rate,
        "avg_delay_s":    round(mean(delays),1) if delays else None,
    }

# 5) Trend-Analyse (Verlauf über Wochen/Monate)
def get_trend_analysis(chat_id: int, periods: int = 4) -> dict:
    """Return weekly totals for the last `periods` weeks."""
    today = datetime.utcnow().date()
    results = []
    with get_db_connection() as conn:
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


# --- Handler-Registrierung ---
def register_statistics_handlers(app):
    init_stats_db()
    app.add_handler(CommandHandler(['stats', 'statistik'], stats_command), group=10)
    async def command_logger(update: Update, context: ContextTypes.DEFAULT_TYPE):
        cmd = update.effective_message.text.split()[0].lstrip('/')
        log_command(update.effective_chat.id, update.effective_user.id, cmd)
    app.add_handler(MessageHandler(filters.COMMAND & ~filters.Regex(r'^/stats'), command_logger), group=9)

