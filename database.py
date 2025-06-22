import os
import logging
from urllib.parse import urlparse
from datetime import date
from typing import List, Dict, Tuple

import psycopg2.pool

# Logger setup
t_logger = logging.getLogger(__name__)

# Initialize connection pool
def _init_pool(dsn: dict, minconn: int = 1, maxconn: int = 10) -> psycopg2.pool.ThreadedConnectionPool:
    try:
        pool_inst = psycopg2.pool.ThreadedConnectionPool(minconn, maxconn, **dsn)
        t_logger.info(f"🔌 Initialized DB pool with {minconn}-{maxconn} connections")
        return pool_inst
    except Exception as e:
        t_logger.error(f"❌ Could not initialize connection pool: {e}")
        raise

# Parse DATABASE_URL and set up pool
db_url = os.getenv("DATABASE_URL")
if not db_url:
    raise ValueError("DATABASE_URL ist nicht gesetzt. Bitte füge das Heroku Postgres Add-on und die Config Vars hinzu.")

parsed = urlparse(db_url)
dsn = {
    'dbname': parsed.path.lstrip('/'),
    'user': parsed.username,
    'password': parsed.password,
    'host': parsed.hostname,
    'port': parsed.port,
    'sslmode': 'require',
}
# Thread-safe pool
_db_pool = _init_pool(dsn, minconn=1, maxconn=10)

# Decorator to acquire and release connections/cursors
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

# --- Schema Initialization and Migrations ---
@_with_cursor
def init_db(cur):
    # Create tables if not exist
    cur.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id BIGINT PRIMARY KEY,
            title TEXT NOT NULL,
            welcome_topic_id BIGINT DEFAULT 0
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS group_settings (
            chat_id BIGINT PRIMARY KEY,
            daily_stats_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            rss_topic_id BIGINT NOT NULL DEFAULT 0,
            mood_question TEXT NOT NULL DEFAULT 'Wie fühlst du dich heute?'
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS welcome (
            chat_id BIGINT PRIMARY KEY,
            photo_id TEXT,
            text TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rules (
            chat_id BIGINT PRIMARY KEY,
            photo_id TEXT,
            text TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS farewell (
            chat_id BIGINT PRIMARY KEY,
            photo_id TEXT,
            text TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rss_feeds (
            chat_id BIGINT,
            url TEXT,
            topic_id BIGINT,
            PRIMARY KEY (chat_id, url)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS last_posts (
            chat_id BIGINT,
            link TEXT,
            PRIMARY KEY (chat_id, link)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_topics (
            chat_id BIGINT,
            user_id BIGINT,
            topic_id BIGINT,
            topic_name TEXT,
            PRIMARY KEY (chat_id, user_id)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS members (
            chat_id BIGINT,
            user_id BIGINT,
            joined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ NULL,
            PRIMARY KEY (chat_id, user_id)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            chat_id BIGINT,
            stat_date DATE,
            user_id BIGINT,
            messages INT DEFAULT 0,
            PRIMARY KEY (chat_id, stat_date, user_id)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mood_meter (
            chat_id BIGINT,
            message_id INT,
            user_id BIGINT,
            mood TEXT,
            PRIMARY KEY(chat_id, message_id, user_id)
        );
    """)

# --- Gruppenverwaltung ---
@_with_cursor
def register_group(cur, chat_id: int, title: str, welcome_topic_id: int = 0):
    cur.execute(
        """
        INSERT INTO groups (chat_id, title, welcome_topic_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title;
        """,
        (chat_id, title, welcome_topic_id)
    )

@_with_cursor
def get_registered_groups(cur) -> List[Tuple[int, str]]:
    cur.execute("SELECT chat_id, title FROM groups;")
    return cur.fetchall()

@_with_cursor
def unregister_group(cur, chat_id: int):
    cur.execute("DELETE FROM groups WHERE chat_id = %s;", (chat_id,))

# --- Mitgliederverwaltung ---
@_with_cursor
def add_member(cur, chat_id: int, user_id: int):
    cur.execute(
        """
        INSERT INTO members (chat_id, user_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING;
        """,
        (chat_id, user_id)
    )
    t_logger.info(f"✅ add_member: user {user_id} zu chat {chat_id} hinzugefügt")

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

# --- Daily Stats ---
@_with_cursor
def inc_message_count(cur, chat_id: int, user_id: int, stat_date: date):
    cur.execute(
        """
        INSERT INTO daily_stats (chat_id, stat_date, user_id, messages)
        VALUES (%s, %s, %s, 1)
        ON CONFLICT (chat_id, stat_date, user_id)
        DO UPDATE SET messages = daily_stats.messages + 1;
        """,
        (chat_id, stat_date, user_id)
    )

@_with_cursor
def get_group_stats(cur, chat_id: int, stat_date: date) -> List[Tuple[int, int]]:
    cur.execute(
        "SELECT user_id, messages FROM daily_stats WHERE chat_id = %s AND stat_date = %s ORDER BY messages DESC LIMIT 3;",
        (chat_id, stat_date)
    )
    return cur.fetchall()

# --- Mood Meter ---
@_with_cursor
def save_mood(cur, chat_id: int, message_id: int, user_id: int, mood: str):
    cur.execute(
        """
        INSERT INTO mood_meter (chat_id, message_id, user_id, mood)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (chat_id, message_id, user_id)
        DO UPDATE SET mood = EXCLUDED.mood;
        """,
        (chat_id, message_id, user_id, mood)
    )

@_with_cursor
def get_mood_counts(cur, chat_id: int, message_id: int) -> Dict[str, int]:
    cur.execute(
        "SELECT mood, COUNT(*) FROM mood_meter WHERE chat_id = %s AND message_id = %s GROUP BY mood;",
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

# --- RSS Feeds ---
@_with_cursor
def set_rss_topic(cur, chat_id: int, topic_id: int):
    cur.execute(
        """
        INSERT INTO group_settings (chat_id, daily_stats_enabled, rss_topic_id)
        VALUES (%s, TRUE, %s)
        ON CONFLICT (chat_id) DO UPDATE SET rss_topic_id = EXCLUDED.rss_topic_id;
        """,
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
def remove_rss_feed(cur, chat_id: int, url: str | None = None):
    if url:
        cur.execute("DELETE FROM rss_feeds WHERE chat_id = %s AND url = %s;", (chat_id, url))
    else:
        cur.execute("DELETE FROM rss_feeds WHERE chat_id = %s;", (chat_id,))

@_with_cursor
def get_rss_feeds(cur) -> List[Tuple[int, str, int]]:
    cur.execute("SELECT chat_id, url, topic_id FROM rss_feeds;")
    return cur.fetchall()

# --- Deduplication ---
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

# Initialize schema
if __name__ == "__main__":
    init_db()
    t_logger.info("✅ Schema initialisiert und Pool bereit.")
