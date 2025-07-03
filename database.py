import os
import logging
from urllib.parse import urlparse
from datetime import date
from typing import List, Dict, Tuple, Optional
from types import SimpleNamespace
import psycopg2
from psycopg2 import pool

# Logger setup
logger = logging.getLogger(__name__)

# --- Connection Pool Setup ---

def _init_pool(dsn: dict, minconn: int = 1, maxconn: int = 10) -> pool.ThreadedConnectionPool:
    try:
        pool_inst = pool.ThreadedConnectionPool(minconn, maxconn, **dsn)
        logger.info(f"🔌 Initialized DB pool with {minconn}-{maxconn} connections")
        return pool_inst
    except Exception as e:
        logger.error(f"❌ Could not initialize connection pool: {e}")
        raise

# Parse DATABASE_URL and configure pool
db_url = os.getenv("DATABASE_URL")
if not db_url:
    raise ValueError(
        "DATABASE_URL ist nicht gesetzt. Bitte füge das Heroku Postgres Add-on und die Config Vars hinzu."
    )
parsed = urlparse(db_url)
dsn = {
    'dbname': parsed.path.lstrip('/'),
    'user': parsed.username,
    'password': parsed.password,
    'host': parsed.hostname,
    'port': parsed.port,
    'sslmode': 'require',
}
_db_pool = _init_pool(dsn, minconn=1, maxconn=10)

# Decorator to acquire/release connections and cursors
def _with_cursor(func):
    def wrapper(*args, **kwargs):
        conn = _db_pool.getconn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                return func(cur, *args, **kwargs)
        finally:
            _db_pool.putconn(conn)
    return wrapper

# --- Schema Initialization & Migrations ---
@_with_cursor
def init_db(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS groups (
            chat_id BIGINT PRIMARY KEY,
            title TEXT NOT NULL,
            welcome_topic_id BIGINT DEFAULT 0
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS group_settings (
            chat_id BIGINT PRIMARY KEY,
            language TEXT NOT NULL DEFAULT 'de',
            daily_stats_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            rss_topic_id BIGINT NOT NULL DEFAULT 0,
            mood_question TEXT NOT NULL DEFAULT 'Wie fühlst du dich heute?'
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS welcome (
            chat_id BIGINT PRIMARY KEY,
            photo_id TEXT,
            text TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rules (
            chat_id BIGINT PRIMARY KEY,
            photo_id TEXT,
            text TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS farewell (
            chat_id BIGINT PRIMARY KEY,
            photo_id TEXT,
            text TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rss_feeds (
            chat_id BIGINT,
            url TEXT,
            topic_id BIGINT,
            PRIMARY KEY (chat_id, url)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS last_posts (
            chat_id BIGINT,
            link TEXT,
            PRIMARY KEY (chat_id, link)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_topics (
            chat_id BIGINT,
            user_id BIGINT,
            topic_id BIGINT,
            topic_name TEXT,
            PRIMARY KEY (chat_id, user_id)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS members (
            chat_id BIGINT,
            user_id BIGINT,
            joined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ NULL,
            PRIMARY KEY (chat_id, user_id)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_stats (
            chat_id BIGINT,
            stat_date DATE,
            user_id BIGINT,
            messages INT DEFAULT 0,
            PRIMARY KEY (chat_id, stat_date, user_id)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mood_meter (
            chat_id BIGINT,
            message_id INT,
            user_id BIGINT,
            mood TEXT,
            PRIMARY KEY(chat_id, message_id, user_id)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS channels (
            id            SERIAL PRIMARY KEY,
            parent_chat_id   BIGINT   NOT NULL,
            channel_id    BIGINT   NOT NULL,
            channel_username TEXT,
            channel_title TEXT,
            created_at    TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    )

# --- Group Management ---
@_with_cursor
def register_group(cur, chat_id: int, title: str, welcome_topic_id: int = 0):
    cur.execute(
        "INSERT INTO groups (chat_id, title, welcome_topic_id) VALUES (%s, %s, %s) "
        "ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title;",
        (chat_id, title, welcome_topic_id)
    )

@_with_cursor
def get_registered_groups(cur) -> List[Tuple[int, str]]:
    cur.execute("SELECT chat_id, title FROM groups;")
    return cur.fetchall()

@_with_cursor
def unregister_group(cur, chat_id: int):
    cur.execute("DELETE FROM groups WHERE chat_id = %s;", (chat_id,))

@_with_cursor
def get_group_setting(cur, chat_id: int) -> Optional[SimpleNamespace]:
    cur.execute("SELECT chat_id, daily_stats_enabled, rss_topic_id, mood_question, language FROM group_settings WHERE chat_id = %s;", (chat_id,))
    row = cur.fetchone()
    if not row:
        return None
    # z.B. return ein kleines Objekt oder NamedTuple
    
    return SimpleNamespace(
        chat_id=row[0],
        daily_stats_enabled=row[1],
        rss_topic_id=row[2],
        mood_question=row[3],
        language=row[4]
    )

@_with_cursor
def set_group_language(cur, chat_id: int, language: str):
    cur.execute(
        "INSERT INTO group_settings (chat_id, language) VALUES (%s, %s) "
        "ON CONFLICT (chat_id) DO UPDATE SET language = EXCLUDED.language;",
        (chat_id, language)
    )

#Channel Management

@_with_cursor
def add_channel(cur, parent_chat_id: int, channel_id: int,
                channel_username: Optional[str] = None,
                channel_title: Optional[str] = None):
    """
    Fügt einen Kanal zur Verwaltung hinzu.
    Ignoriert Einträge, die schon existieren.
    """
    cur.execute(
        """
        INSERT INTO channels (parent_chat_id, channel_id, channel_username, channel_title)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT DO NOTHING;
        """,
        (parent_chat_id, channel_id, channel_username, channel_title)
    )

@_with_cursor
def remove_channel(cur, parent_chat_id: int, channel_id: int):
    """
    Entfernt einen Kanal aus der Verwaltung.
    """
    cur.execute(
        "DELETE FROM channels WHERE parent_chat_id = %s AND channel_id = %s;",
        (parent_chat_id, channel_id)
    )

@_with_cursor
def list_channels(cur, parent_chat_id: int) -> List[Tuple[int, Optional[str], Optional[str]]]:
    """
    Gibt alle Kanäle zurück, die in der Gruppe (chat_id) registriert sind.
    Liefert Tuples: (channel_id, channel_username, channel_title).
    """
    cur.execute(
        """
        SELECT channel_id, channel_username, channel_title
        FROM channels
        WHERE parent_chat_id = %s;
        """,
        (parent_chat_id,)
    )
    return cur.fetchall()

@_with_cursor
def get_all_channels(cur) -> List[Tuple[int, Optional[str], Optional[str]]]:
    """
    Liefert eine Liste aller in der Datenbank registrierten Kanäle.
    """
    cur.execute(
        """
        SELECT parent_chat_id, channel_id, channel_username, channel_title
        FROM channels;
        """
    )
    return cur.fetchall()

# ─── Geplante Kanal-Beiträge ─────────────────────────────────────────
@_with_cursor
def add_scheduled_post(cur, channel_id: int, post_text: str, schedule_cron: str):
    """
    Speichert einen geplanten Beitrag für einen Kanal.
    `schedule_cron` z.B. "0 9 * * *" für täglich 9:00 Uhr.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_schedules (
            channel_id   BIGINT,
            post_text    TEXT,
            schedule_cron TEXT,
            PRIMARY KEY (channel_id, post_text)
        );
        """
    )
    cur.execute(
        """
        INSERT INTO channel_schedules (channel_id, post_text, schedule_cron)
        VALUES (%s, %s, %s)
        ON CONFLICT (channel_id, post_text) DO UPDATE
        SET schedule_cron = EXCLUDED.schedule_cron;
        """,
        (channel_id, post_text, schedule_cron)
    )

@_with_cursor
def list_scheduled_posts(cur, channel_id: int) -> List[Tuple[str, str]]:
    cur.execute(
        "SELECT post_text, schedule_cron FROM channel_schedules WHERE channel_id = %s;",
        (channel_id,)
    )
    return cur.fetchall()

# --- Member Management ---
@_with_cursor
def add_member(cur, chat_id: int, user_id: int):
    cur.execute(
        "INSERT INTO members (chat_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING;",
        (chat_id, user_id)
    )
    logger.info(f"✅ add_member: user {user_id} zu chat {chat_id} hinzugefügt")

@_with_cursor
def remove_member(cur, chat_id: int, user_id: int):
    cur.execute(
        "DELETE FROM members WHERE chat_id = %s AND user_id = %s;",
        (chat_id, user_id)
    )

@_with_cursor
def list_members(cur, chat_id: int) -> List[int]:
    cur.execute(
        "SELECT user_id FROM members WHERE chat_id = %s AND is_deleted = FALSE;",
        (chat_id,)
    )
    return [row[0] for row in cur.fetchall()]

@_with_cursor
def count_members(cur, chat_id: int) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM members WHERE chat_id = %s AND is_deleted = FALSE;",
        (chat_id,)
    )
    return cur.fetchone()[0] or 0

@_with_cursor
def get_new_members_count(cur, chat_id: int, d: date) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM members WHERE chat_id = %s AND DATE(joined_at) = %s;",
        (chat_id, d)
    )
    return cur.fetchone()[0]

@_with_cursor
def mark_member_deleted(cur, chat_id: int, user_id: int):
    cur.execute(
        "UPDATE members SET is_deleted = TRUE, deleted_at = CURRENT_TIMESTAMP "
        "WHERE chat_id = %s AND user_id = %s;",
        (chat_id, user_id)
    )

@_with_cursor
def list_active_members(cur, chat_id: int) -> List[int]:
    cur.execute(
        "SELECT user_id FROM members WHERE chat_id = %s AND is_deleted = FALSE;",
        (chat_id,)
    )
    return [row[0] for row in cur.fetchall()]

@_with_cursor
def purge_deleted_members(cur, chat_id: Optional[int] = None):
    if chat_id is None:
        cur.execute("DELETE FROM members WHERE is_deleted = TRUE;")
    else:
        cur.execute(
            "DELETE FROM members WHERE chat_id = %s AND is_deleted = TRUE;",
            (chat_id,)
        )

# --- Themenzuweisung für Linksperre-Ausnahme ---
@_with_cursor
def assign_topic(cur, chat_id: int, user_id: int, topic_id: int = 0, topic_name: Optional[str] = None):
    cur.execute(
        "INSERT INTO user_topics (chat_id, user_id, topic_id, topic_name) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (chat_id, user_id) DO UPDATE SET topic_id = EXCLUDED.topic_id, topic_name = EXCLUDED.topic_name;",
        (chat_id, user_id, topic_id, topic_name)
    )

@_with_cursor
def remove_topic(cur, chat_id: int, user_id: int):
    cur.execute("DELETE FROM user_topics WHERE chat_id = %s AND user_id = %s;", (chat_id, user_id))

@_with_cursor
def has_topic(cur, chat_id: int, user_id: int) -> bool:
    cur.execute("SELECT 1 FROM user_topics WHERE chat_id = %s AND user_id = %s;", (chat_id, user_id))
    return cur.fetchone() is not None

@_with_cursor
def get_topic_owners(cur, chat_id: int) -> List[int]:
    cur.execute("SELECT user_id FROM user_topics WHERE chat_id = %s;", (chat_id,))
    return [row[0] for row in cur.fetchall()]

# --- Daily Stats ---
@_with_cursor
def inc_message_count(cur, chat_id: int, user_id: int, stat_date: date):
    cur.execute(
        "INSERT INTO daily_stats (chat_id, stat_date, user_id, messages) VALUES (%s, %s, %s, 1) "
        "ON CONFLICT (chat_id, stat_date, user_id) DO UPDATE SET messages = daily_stats.messages + 1;",
        (chat_id, stat_date, user_id)
    )

@_with_cursor
def get_group_stats(cur, chat_id: int, stat_date: date) -> List[Tuple[int, int]]:
    cur.execute(
        "SELECT user_id, messages FROM daily_stats "
        "WHERE chat_id = %s AND stat_date = %s ORDER BY messages DESC LIMIT 3;",
        (chat_id, stat_date)
    )
    return cur.fetchall()

@_with_cursor
def is_daily_stats_enabled(cur, chat_id: int) -> bool:
    cur.execute(
        "SELECT daily_stats_enabled FROM group_settings WHERE chat_id = %s",
        (chat_id,)
    )
    row = cur.fetchone()
    return row[0] if row else True  # Default = True

@_with_cursor
def set_daily_stats(cur, chat_id: int, enabled: bool):
    cur.execute(
        """
        INSERT INTO group_settings(chat_id, daily_stats_enabled)
        VALUES (%s, %s)
        ON CONFLICT (chat_id) DO UPDATE
        SET daily_stats_enabled = EXCLUDED.daily_stats_enabled;
        """,
        (chat_id, enabled)
    )

# --- Mood Meter ---
@_with_cursor
def save_mood(cur, chat_id: int, message_id: int, user_id: int, mood: str):
    cur.execute(
        "INSERT INTO mood_meter (chat_id, message_id, user_id, mood) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (chat_id, message_id, user_id) DO UPDATE SET mood = EXCLUDED.mood;",
        (chat_id, message_id, user_id, mood)
    )

@_with_cursor
def get_mood_counts(cur, chat_id: int, message_id: int) -> Dict[str, int]:
    cur.execute(
        "SELECT mood, COUNT(*) FROM mood_meter "
        "WHERE chat_id = %s AND message_id = %s GROUP BY mood;",
        (chat_id, message_id)
    )
    return dict(cur.fetchall())

@_with_cursor
def get_mood_question(cur, chat_id: int) -> str:
    cur.execute(
        "SELECT mood_question FROM group_settings WHERE chat_id = %s;",
        (chat_id,)
    )
    row = cur.fetchone()
    return row[0] if row else "Wie fühlst du dich heute?"

@_with_cursor
def set_mood_question(cur, chat_id: int, question: str):
    cur.execute(
        "INSERT INTO group_settings (chat_id, mood_question) VALUES (%s, %s) "
        "ON CONFLICT (chat_id) DO UPDATE SET mood_question = EXCLUDED.mood_question;",
        (chat_id, question)
    )

# --- Welcome / Rules / Farewell ---
@_with_cursor
def set_welcome(cur, chat_id: int, photo_id: Optional[str], text: Optional[str]):
    cur.execute(
        "INSERT INTO welcome (chat_id, photo_id, text) VALUES (%s, %s, %s) "
        "ON CONFLICT (chat_id) DO UPDATE SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;",
        (chat_id, photo_id, text)
    )

@_with_cursor
def get_welcome(cur, chat_id: int) -> Optional[Tuple[str, str]]:
    cur.execute("SELECT photo_id, text FROM welcome WHERE chat_id = %s;", (chat_id,))
    return cur.fetchone()

@_with_cursor
def delete_welcome(cur, chat_id: int):
    cur.execute("DELETE FROM welcome WHERE chat_id = %s;", (chat_id,))

@_with_cursor
def set_rules(cur, chat_id: int, photo_id: Optional[str], text: Optional[str]):
    cur.execute(
        "INSERT INTO rules (chat_id, photo_id, text) VALUES (%s, %s, %s) "
        "ON CONFLICT (chat_id) DO UPDATE SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;",
        (chat_id, photo_id, text)
    )

@_with_cursor
def get_rules(cur, chat_id: int) -> Optional[Tuple[str, str]]:
    cur.execute("SELECT photo_id, text FROM rules WHERE chat_id = %s;", (chat_id,))
    return cur.fetchone()

@_with_cursor
def delete_rules(cur, chat_id: int):
    cur.execute("DELETE FROM rules WHERE chat_id = %s;", (chat_id,))

@_with_cursor
def set_farewell(cur, chat_id: int, photo_id: Optional[str], text: Optional[str]):
    cur.execute(
        "INSERT INTO farewell (chat_id, photo_id, text) VALUES (%s, %s, %s) "
        "ON CONFLICT (chat_id) DO UPDATE SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;",
        (chat_id, photo_id, text)
    )

@_with_cursor
def get_farewell(cur, chat_id: int) -> Optional[Tuple[str, str]]:
    cur.execute("SELECT photo_id, text FROM farewell WHERE chat_id = %s;", (chat_id,))
    return cur.fetchone()

@_with_cursor
def delete_farewell(cur, chat_id: int):
    cur.execute("DELETE FROM farewell WHERE chat_id = %s;", (chat_id,))

# --- RSS Feeds & Deduplication ---
@_with_cursor
def set_rss_topic(cur, chat_id: int, topic_id: int):
    cur.execute(
        "INSERT INTO group_settings (chat_id, daily_stats_enabled, rss_topic_id) VALUES (%s, TRUE, %s) "
        "ON CONFLICT (chat_id) DO UPDATE SET rss_topic_id = EXCLUDED.rss_topic_id;",
        (chat_id, topic_id)
    )

@_with_cursor
def get_rss_topic(cur, chat_id: int) -> int:
    cur.execute("SELECT rss_topic_id FROM group_settings WHERE chat_id = %s;", (chat_id,))
    row = cur.fetchone()
    return row[0] if row else 0

@_with_cursor
def add_rss_feed(cur, chat_id: int, url: str, topic_id: int):
    cur.execute(
        "INSERT INTO rss_feeds (chat_id, url, topic_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING;",
        (chat_id, url, topic_id)
    )

@_with_cursor
def list_rss_feeds(cur, chat_id: int) -> List[Tuple[str, int]]:
    cur.execute("SELECT url, topic_id FROM rss_feeds WHERE chat_id = %s;", (chat_id,))
    return cur.fetchall()

@_with_cursor
def remove_rss_feed(cur, chat_id: int, url: Optional[str] = None):
    if url:
        cur.execute("DELETE FROM rss_feeds WHERE chat_id = %s AND url = %s;", (chat_id, url))
    else:
        cur.execute("DELETE FROM rss_feeds WHERE chat_id = %s;", (chat_id,))

@_with_cursor
def get_rss_feeds(cur) -> List[Tuple[int, str, int]]:
    cur.execute("SELECT chat_id, url, topic_id FROM rss_feeds;" )
    return cur.fetchall()

@_with_cursor
def get_posted_links(cur, chat_id: int) -> set:
    cur.execute("SELECT link FROM last_posts WHERE chat_id = %s;", (chat_id,))
    return {row[0] for row in cur.fetchall()}

@_with_cursor
def add_posted_link(cur, chat_id: int, link: str):
    cur.execute(
        "INSERT INTO last_posts (chat_id, link) VALUES (%s, %s) ON CONFLICT DO NOTHING;",
        (chat_id, link)
    )

# --- Legacy Migration Utility ---
def migrate_db():
    import psycopg2
    logging.basicConfig(level=logging.INFO)
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    try:
        logging.info("Starte Migration für bestehende Tabellen...")
        cur.execute(
            "ALTER TABLE groups ADD COLUMN IF NOT EXISTS welcome_topic_id BIGINT DEFAULT 0;"
        )
        # Weitere ALTER-Befehle hier...
        conn.commit()
        logging.info("Migration erfolgreich abgeschlossen.")
    except Exception as e:
        logging.error(f"Migration fehlgeschlagen: {e}")
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

# --- Entry Point ---
if __name__ == "__main__":
    init_db()
    logger.info("✅ Schema initialisiert und Pool bereit.")
