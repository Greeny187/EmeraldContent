import os
import logging
from urllib.parse import urlparse
from datetime import date
from typing import List, Dict, Tuple, Optional, Any
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
    def wrapped(*args, **kwargs):
        conn = _db_pool.getconn()
        try:
            with conn.cursor() as cur:
                result = func(cur, *args, **kwargs)
                # immer committen, damit z.B. add_posted_link tatsächlich gespeichert wird
                conn.commit()
                return result
        finally:
            # Immer sicherstellen, dass die Verbindung zurückgegeben wird
            _db_pool.putconn(conn)
    return wrapped

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
        CREATE TABLE IF NOT EXISTS translations_cache (
            source_text   TEXT    NOT NULL,
            language_code TEXT    NOT NULL,
            translated    TEXT    NOT NULL,
            is_override   BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (source_text, language_code)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS group_settings (
            chat_id BIGINT PRIMARY KEY,
            title TEXT NOT NULL,
            daily_stats_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            rss_topic_id BIGINT NOT NULL DEFAULT 0,
            mood_question TEXT NOT NULL DEFAULT 'Wie fühlst du dich heute?',
            mood_topic_id BIGINT NOT NULL DEFAULT 0,
            language_code TEXT NOT NULL DEFAULT 'de',
            captcha_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            captcha_type TEXT NOT NULL DEFAULT 'button',
            captcha_behavior TEXT NOT NULL DEFAULT 'kick',
            link_protection_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
            link_warning_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
            link_warning_text        TEXT    NOT NULL DEFAULT '⚠️ Nur Admins dürfen Links posten.',
            link_exceptions_enabled  BOOLEAN NOT NULL DEFAULT TRUE
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
            last_etag TEXT,
            last_modified TEXT,
            post_images BOOLEAN DEFAULT FALSE,
            enabled BOOLEAN DEFAULT TRUE
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
            members INT,
            admins INT,
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
        CREATE TABLE IF NOT EXISTS spam_policy (
            chat_id BIGINT PRIMARY KEY,
            level TEXT NOT NULL DEFAULT 'off',       
            link_whitelist TEXT[] DEFAULT '{}',
            user_whitelist BIGINT[] DEFAULT '{}',
            domain_blacklist TEXT[] DEFAULT '{}',
            emoji_max_per_msg INT DEFAULT 20,
            emoji_max_per_min INT DEFAULT 60,
            max_msgs_per_10s INT DEFAULT 7,
            new_member_link_block BOOLEAN DEFAULT TRUE,
            action_primary TEXT DEFAULT 'delete',    
            action_secondary TEXT DEFAULT 'mute',    
            escalation_threshold INT DEFAULT 3,      
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS night_mode (
            chat_id BIGINT PRIMARY KEY,
            enabled BOOLEAN DEFAULT FALSE,
            start_minute INT DEFAULT 1320,  -- 22:00 => 22*60
            end_minute INT DEFAULT 360,     -- 06:00 => 6*60
            delete_non_admin_msgs BOOLEAN DEFAULT TRUE,
            warn_once BOOLEAN DEFAULT TRUE,
            timezone TEXT DEFAULT 'Europe/Berlin'
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reply_times (
            chat_id BIGINT,
            question_msg_id BIGINT,
            question_user BIGINT,
            answer_msg_id BIGINT,
            answer_user BIGINT,
            delta_ms BIGINT,
            ts TIMESTAMP DEFAULT NOW()
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS auto_responses (
            chat_id BIGINT,
            trigger TEXT,
            matched_confidence NUMERIC,
            used_snippet TEXT,
            latency_ms BIGINT,
            ts TIMESTAMP DEFAULT NOW()
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS topics_vocab (
            chat_id BIGINT,
            topic_id INT,
            keywords TEXT[],
            PRIMARY KEY (chat_id, topic_id)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS faq_snippets (
            chat_id BIGINT,
            trigger TEXT,
            answer TEXT,
            PRIMARY KEY (chat_id, trigger)
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
    cur.execute(
        "INSERT INTO group_settings (chat_id, title) VALUES (%s, %s) ON CONFLICT DO NOTHING;",
        (chat_id, title)
    )

@_with_cursor
def get_registered_groups(cur) -> List[Tuple[int, str]]:
    cur.execute("SELECT chat_id, title FROM groups;")
    return cur.fetchall()

@_with_cursor
def unregister_group(cur, chat_id: int):
    cur.execute("DELETE FROM groups WHERE chat_id = %s;", (chat_id,))

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

@_with_cursor
def get_link_settings(cur, chat_id: int) -> Tuple[bool, bool, str, bool]:
    cur.execute("""
        SELECT link_protection_enabled, link_warning_enabled, link_warning_text, link_exceptions_enabled
        FROM group_settings WHERE chat_id=%s;
    """, (chat_id,))
    row = cur.fetchone()
    # falls chat_id noch nicht existiert, Default-Werte zurückgeben
    return row if row else (False, False,
                             '⚠️ Nur Admins dürfen Links posten.',
                             True)

@_with_cursor
def set_link_settings(cur, chat_id: int,
                        protection: Optional[bool] = None,
                        warning_on: Optional[bool] = None,
                        warning_text: Optional[str] = None,
                        exceptions_on: Optional[bool] = None):
    # Baue dynamisches UPDATE
    parts, params = [], []
    if protection is not None:
        parts.append("link_protection_enabled = %s");   params.append(protection)
    if warning_on is not None:
        parts.append("link_warning_enabled = %s");      params.append(warning_on)
    if warning_text is not None:
        parts.append("link_warning_text = %s");         params.append(warning_text)
    if exceptions_on is not None:
        parts.append("link_exceptions_enabled = %s");   params.append(exceptions_on)
    if not parts:
        return
    sql = "INSERT INTO group_settings(chat_id) VALUES (%s) ON CONFLICT (chat_id) DO UPDATE SET "
    sql += ", ".join(parts)
    params = [chat_id] + params
    cur.execute(sql, params)

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
@_with_cursor
def set_mood_topic(cur, chat_id: int, topic_id: int):
    cur.execute(
      "INSERT INTO group_settings (chat_id, daily_stats_enabled, rss_topic_id, mood_topic_id) "
      "VALUES (%s, TRUE, COALESCE((SELECT rss_topic_id FROM group_settings WHERE chat_id=%s), 0), %s) "
      "ON CONFLICT (chat_id) DO UPDATE SET mood_topic_id = EXCLUDED.mood_topic_id;",
      (chat_id, chat_id, topic_id)
    )

@_with_cursor
def get_mood_topic(cur, chat_id: int) -> int:
    cur.execute("SELECT mood_topic_id FROM group_settings WHERE chat_id = %s;", (chat_id,))
    row = cur.fetchone()
    return row[0] if row else 0

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

@ _with_cursor
def get_captcha_settings(cur, chat_id: int):
    cur.execute(
        "SELECT captcha_enabled, captcha_type, captcha_behavior FROM group_settings WHERE chat_id=%s",
        (chat_id,)
    )
    return cur.fetchone() or (False, 'button', 'kick')

@ _with_cursor
def set_captcha_settings(cur, chat_id: int, enabled: bool, ctype: str, behavior: str):
    cur.execute(
        """
        INSERT INTO group_settings (chat_id, captcha_enabled, captcha_type, captcha_behavior)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (chat_id) DO UPDATE
          SET captcha_enabled  = EXCLUDED.captcha_enabled,
              captcha_type     = EXCLUDED.captcha_type,
              captcha_behavior = EXCLUDED.captcha_behavior;
        """,
        (chat_id, enabled, ctype, behavior)
    )

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
def get_posted_links(cur, chat_id: int) -> list:
    cur.execute("SELECT link FROM last_posts WHERE chat_id = %s ORDER BY posted_at DESC;", (chat_id,))
    return [row[0] for row in cur.fetchall()]

@_with_cursor
def add_posted_link(cur, chat_id: int, link: str):
    cur.execute(
        "INSERT INTO last_posts (chat_id, link, posted_at) VALUES (%s, %s, NOW()) ON CONFLICT DO NOTHING;",
        (chat_id, link)
    )

@_with_cursor
def get_last_posted_link(cur, chat_id: int, feed_url: str) -> str:
    cur.execute("SELECT link FROM last_posts WHERE chat_id = %s AND feed_url = %s;", (chat_id, feed_url))
    row = cur.fetchone()
    return row[0] if row else None

@_with_cursor
def set_last_posted_link(cur, chat_id: int, feed_url: str, link: str):
    cur.execute("""
        INSERT INTO last_posts (chat_id, feed_url, link, posted_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (chat_id, feed_url) DO UPDATE
            SET link = EXCLUDED.link, posted_at = EXCLUDED.posted_at;
    """, (chat_id, feed_url, link))

def prune_posted_links(chat_id, keep_last=100):
    with _db_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM last_posts
                 WHERE chat_id = %s
                   AND link NOT IN (
                       SELECT link FROM last_posts
                        WHERE chat_id = %s
                        ORDER BY posted_at DESC
                        LIMIT %s
                   )
            """, (chat_id, chat_id, keep_last))

def get_all_group_ids():
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id FROM group_settings")
            return [row[0] for row in cur.fetchall()]
    finally:
        _db_pool.putconn(conn)

# Mulitlanguage

@_with_cursor
def get_cached_translation(cur, source_text: str, lang: str) -> Optional[str]:
    cur.execute(
        "SELECT translated FROM translations_cache "
        "WHERE source_text=%s AND language_code=%s;",
        (source_text, lang)
    )
    row = cur.fetchone()
    return row[0] if row else None

@_with_cursor
def set_cached_translation(cur, source_text: str, lang: str,
                           translated: str, override: bool=False):
    cur.execute(
        """
        INSERT INTO translations_cache
          (source_text, language_code, translated, is_override)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (source_text, language_code) DO UPDATE
          SET translated = EXCLUDED.translated,
              is_override = EXCLUDED.is_override;
        """,
        (source_text, lang, translated, override)
    )

@_with_cursor
def get_group_language(cur, chat_id: int) -> str:
    cur.execute(
        "SELECT language_code FROM group_settings WHERE chat_id = %s;",
        (chat_id,)
    )
    row = cur.fetchone()
    return row[0] if row else 'de'

@_with_cursor
def set_group_language(cur, chat_id: int, lang: str):
    cur.execute(
        "INSERT INTO group_settings (chat_id, language_code) VALUES (%s, %s) "
        "ON CONFLICT (chat_id) DO UPDATE SET language_code = EXCLUDED.language_code;",
        (chat_id, lang)
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
        cur.execute(
            "ALTER TABLE last_posts ADD COLUMN IF NOT EXISTS posted_at TIMESTAMP DEFAULT NOW();"
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_last_posts_feed ON last_posts(chat_id, feed_url);"
        )
        cur.execute(
            "ALTER TABLE last_posts ADD COLUMN IF NOT EXISTS feed_url TEXT;"
        )
        cur.execute(
            "ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS language_code TEXT NOT NULL DEFAULT 'de';"
        )
        cur.execute(
            "ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS title TEXT NOT NULL;"
        )
        cur.execute(
            "ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS captcha_enabled BOOLEAN NOT NULL DEFAULT FALSE;"
        )
        cur.execute(
            "ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS captcha_type TEXT NOT NULL DEFAULT 'button';"
        )
        cur.execute(
            "ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS captcha_behavior TEXT NOT NULL DEFAULT 'kick';"
        )
        cur.execute("""
        ALTER TABLE group_settings
        ADD COLUMN IF NOT EXISTS link_protection_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS link_warning_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS link_warning_text       TEXT    NOT NULL DEFAULT '⚠️ Nur Admins dürfen Links posten.',
        ADD COLUMN IF NOT EXISTS link_exceptions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        ADD COLUMN IF NOT EXISTS mood_topic_id BIGINT NOT NULL DEFAULT 0;
    """)
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
