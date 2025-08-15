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
            link_exceptions_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
            ai_faq_enabled   BOOLEAN NOT NULL DEFAULT FALSE,
            ai_rss_summary   BOOLEAN NOT NULL DEFAULT FALSE
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
            enabled BOOLEAN DEFAULT TRUE,
            PRIMARY KEY (chat_id, url)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS last_posts (
            chat_id   BIGINT,
            feed_url  TEXT,
            link      TEXT,
            posted_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (chat_id, feed_url)
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
            timezone TEXT DEFAULT 'Europe/Berlin',
            -- Neu: Spalten, die sonst nur Migration ergänzt
            hard_mode BOOLEAN NOT NULL DEFAULT FALSE,
            override_until TIMESTAMPTZ NULL
        );
        """
    )

    # (Optional) sinnvolle Indizes für bessere Abfragen
    cur.execute("CREATE INDEX IF NOT EXISTS idx_message_logs_chat_ts ON message_logs(chat_id, timestamp DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_member_events_chat_ts ON member_events(chat_id, ts DESC);")
    
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
            was_helpful BOOLEAN,
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
    # ADD MISSING TABLES FOR STATISTICS
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS message_logs (
            chat_id BIGINT,
            user_id BIGINT,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS member_events (
            chat_id BIGINT,
            user_id BIGINT,
            ts TIMESTAMPTZ DEFAULT NOW(),
            event_type TEXT -- 'join', 'leave', 'kick'
        );
        """
    )

    # ---- Alt-Schema absichern (Legacy-DBs) ----
    cur.execute("ALTER TABLE message_logs  ADD COLUMN IF NOT EXISTS chat_id  BIGINT;")
    cur.execute("ALTER TABLE message_logs  ADD COLUMN IF NOT EXISTS user_id  BIGINT;")
    cur.execute("ALTER TABLE message_logs  ADD COLUMN IF NOT EXISTS timestamp TIMESTAMPTZ DEFAULT NOW();")

    cur.execute("ALTER TABLE member_events ADD COLUMN IF NOT EXISTS chat_id  BIGINT;")
    cur.execute("ALTER TABLE member_events ADD COLUMN IF NOT EXISTS user_id  BIGINT;")
    cur.execute("ALTER TABLE member_events ADD COLUMN IF NOT EXISTS ts      TIMESTAMPTZ DEFAULT NOW();")
    cur.execute("ALTER TABLE member_events ADD COLUMN IF NOT EXISTS event_type TEXT;")

    # ADD MISSING EVENT TABLES HERE
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS spam_events (
            event_id BIGSERIAL PRIMARY KEY,
            chat_id BIGINT,
            user_id BIGINT,
            ts TIMESTAMPTZ DEFAULT NOW(),
            reason TEXT,
            action TEXT,
            message_id BIGINT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS night_events (
            event_id BIGSERIAL PRIMARY KEY,
            chat_id BIGINT,
            ts TIMESTAMPTZ DEFAULT NOW(),
            kind TEXT, -- 'delete', 'warn'
            count INT
        );
        """
    )
    
@_with_cursor
def migrate_stats_rollup(cur):
    # Tages-Rollup pro Gruppe & Datum
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agg_group_day (
          chat_id          BIGINT,
          stat_date        DATE,
          messages_total   INT,
          active_users     INT,
          joins            INT,
          leaves           INT,
          kicks            INT,
          reply_median_ms  BIGINT,
          reply_p90_ms     BIGINT,
          autoresp_hits    INT,
          autoresp_helpful INT,
          spam_actions     INT,
          night_deletes    INT,
          PRIMARY KEY (chat_id, stat_date)
        );
    """)

    # **Neu:** Legacy-Spalten sicher nachziehen, bevor Indizes erstellt werden
    cur.execute("ALTER TABLE reply_times    ADD COLUMN IF NOT EXISTS ts TIMESTAMP DEFAULT NOW();")
    cur.execute("ALTER TABLE auto_responses ADD COLUMN IF NOT EXISTS ts TIMESTAMP DEFAULT NOW();")

    # sinnvolle Indizes auf Rohdaten (idempotent)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reply_times_chat_ts    ON reply_times(chat_id, ts DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_auto_responses_chat_ts ON auto_responses(chat_id, ts DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_auto_responses_trigger ON auto_responses(chat_id, trigger);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_spam_events_chat_ts    ON spam_events(chat_id, ts DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_night_events_chat_ts   ON night_events(chat_id, ts DESC);")

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

@_with_cursor
def ensure_forum_topics_schema(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS forum_topics (
          chat_id   BIGINT,
          topic_id  BIGINT,
          name      TEXT,
          last_seen TIMESTAMPTZ DEFAULT NOW(),
          PRIMARY KEY (chat_id, topic_id)
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_forum_topics_seen ON forum_topics(chat_id, last_seen DESC);")

@_with_cursor
def upsert_forum_topic(cur, chat_id:int, topic_id:int, name:str|None=None):
    cur.execute("""
        INSERT INTO forum_topics (chat_id, topic_id, name, last_seen)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (chat_id, topic_id) DO UPDATE
           SET name = COALESCE(EXCLUDED.name, forum_topics.name),
               last_seen = NOW();
    """, (chat_id, topic_id, name))

@_with_cursor
def rename_forum_topic(cur, chat_id:int, topic_id:int, new_name:str):
    cur.execute("""
        UPDATE forum_topics
           SET name=%s, last_seen=NOW()
         WHERE chat_id=%s AND topic_id=%s;
    """, (new_name, chat_id, topic_id))

@_with_cursor
def list_forum_topics(cur, chat_id:int, limit:int=50, offset:int=0):
    cur.execute("""
        SELECT topic_id, COALESCE(NULLIF(name, ''), CONCAT('Topic ', topic_id)) AS name, last_seen
          FROM forum_topics
         WHERE chat_id=%s
         ORDER BY last_seen DESC
         LIMIT %s OFFSET %s;
    """, (chat_id, limit, offset))
    return cur.fetchall()

@_with_cursor
def count_forum_topics(cur, chat_id:int) -> int:
    cur.execute("SELECT COUNT(*) FROM forum_topics WHERE chat_id=%s;", (chat_id,))
    return cur.fetchone()[0]

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

@_with_cursor
def record_reply_time(cur, chat_id: int,
                      question_msg_id: int, question_user: int,
                      answer_msg_id: int, answer_user: int,
                      delta_ms: int):
    cur.execute("""
        INSERT INTO reply_times (
            chat_id, question_msg_id, question_user,
            answer_msg_id, answer_user, delta_ms, ts
        ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT DO NOTHING;
    """, (chat_id, question_msg_id, question_user, answer_msg_id, answer_user, delta_ms))

def _ensure_agg_group_day(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agg_group_day (
          chat_id BIGINT,
          stat_date DATE,
          messages_total INT,
          active_users INT,
          joins INT,
          leaves INT,
          kicks INT,
          reply_median_ms BIGINT,
          reply_p90_ms BIGINT,
          autoresp_hits INT,
          autoresp_helpful INT,
          spam_actions INT,
          night_deletes INT,
          PRIMARY KEY (chat_id, stat_date)
        );
    """)

@_with_cursor
def upsert_agg_group_day(cur, chat_id:int, stat_date, payload:dict):
    cur.execute("""
        INSERT INTO agg_group_day (
            chat_id, stat_date, messages_total, active_users, joins, leaves, kicks,
            reply_median_ms, reply_p90_ms, autoresp_hits, autoresp_helpful, spam_actions, night_deletes
        ) VALUES (%(chat_id)s, %(stat_date)s, %(messages_total)s, %(active_users)s, %(joins)s, %(leaves)s, %(kicks)s,
                  %(reply_median_ms)s, %(reply_p90_ms)s, %(autoresp_hits)s, %(autoresp_helpful)s, %(spam_actions)s, %(night_deletes)s)
        ON CONFLICT (chat_id, stat_date) DO UPDATE SET
            messages_total=EXCLUDED.messages_total,
            active_users=EXCLUDED.active_users,
            joins=EXCLUDED.joins, leaves=EXCLUDED.leaves, kicks=EXCLUDED.kicks,
            reply_median_ms=EXCLUDED.reply_median_ms, reply_p90_ms=EXCLUDED.reply_p90_ms,
            autoresp_hits=EXCLUDED.autoresp_hits, autoresp_helpful=EXCLUDED.autoresp_helpful,
            spam_actions=EXCLUDED.spam_actions, night_deletes=EXCLUDED.night_deletes;
    """, dict(payload, chat_id=chat_id, stat_date=stat_date))

@_with_cursor
def compute_agg_group_day(cur, chat_id:int, stat_date):
    # Start/Ende UTC für den Tag
    cur.execute("SELECT %s::date, (%s::date + INTERVAL '1 day')", (stat_date, stat_date))
    d0, d1 = cur.fetchone()

    # messages_total & active_users – zuerst daily_stats versuchen, sonst message_logs
    cur.execute("""
        WITH s AS (
          SELECT COALESCE(SUM(messages),0) AS m, COUNT(DISTINCT user_id) AS au
          FROM daily_stats
          WHERE chat_id=%s AND stat_date=%s
        )
        SELECT m, au FROM s
    """, (chat_id, d0))
    row = cur.fetchone() or (0,0)
    messages_total, active_users = row if row != (None, None) else (0,0)

    if messages_total == 0:
        cur.execute("""
            SELECT COUNT(*), COUNT(DISTINCT user_id)
            FROM message_logs
            WHERE chat_id=%s AND timestamp >= %s AND timestamp < %s
        """, (chat_id, d0, d1))
        messages_total, active_users = cur.fetchone() or (0,0)

    # member events
    cur.execute("""
        SELECT
          COUNT(*) FILTER (WHERE event_type='join')  AS joins,
          COUNT(*) FILTER (WHERE event_type='leave') AS leaves,
          COUNT(*) FILTER (WHERE event_type='kick')  AS kicks
        FROM member_events
        WHERE chat_id=%s AND ts >= %s AND ts < %s
    """, (chat_id, d0, d1))
    joins, leaves, kicks = cur.fetchone() or (0,0,0)

    # reply percentiles
    cur.execute("""
        SELECT
          PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY delta_ms),
          PERCENTILE_DISC(0.9) WITHIN GROUP (ORDER BY delta_ms)
        FROM reply_times
        WHERE chat_id=%s AND ts >= %s AND ts < %s
    """, (chat_id, d0, d1))
    p50, p90 = cur.fetchone() or (None, None)

    # autoresponses
    cur.execute("""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE was_helpful IS TRUE)
        FROM auto_responses
        WHERE chat_id=%s AND ts >= %s AND ts < %s
    """, (chat_id, d0, d1))
    ar_hits, ar_helpful = cur.fetchone() or (0,0)

    # moderation
    cur.execute("SELECT COUNT(*) FROM spam_events WHERE chat_id=%s AND ts >= %s AND ts < %s",
                (chat_id, d0, d1))
    spam_actions = cur.fetchone()[0] if cur.rowcount != -1 else 0

    # night deletes
    cur.execute("""
        SELECT COALESCE(SUM(count),0)
        FROM night_events
        WHERE chat_id=%s AND kind='delete' AND ts >= %s AND ts < %s
    """, (chat_id, d0, d1))
    night_deletes = cur.fetchone()[0] or 0

    return {
        "messages_total":   int(messages_total or 0),
        "active_users":     int(active_users or 0),
        "joins":            int(joins or 0),
        "leaves":           int(leaves or 0),
        "kicks":            int(kicks or 0),
        "reply_median_ms":  int(p50) if p50 is not None else None,
        "reply_p90_ms":     int(p90) if p90 is not None else None,
        "autoresp_hits":    int(ar_hits or 0),
        "autoresp_helpful": int(ar_helpful or 0),
        "spam_actions":     int(spam_actions or 0),
        "night_deletes":    int(night_deletes or 0),
    }

@_with_cursor
def get_agg_summary(cur, chat_id:int, d_start, d_end):
    _ensure_agg_group_day(cur)
    cur.execute("""
        SELECT
          COALESCE(SUM(messages_total),0),
          COALESCE(SUM(active_users),0),
          COALESCE(SUM(joins),0),
          COALESCE(SUM(leaves),0),
          COALESCE(SUM(kicks),0),
          COALESCE(SUM(autoresp_hits),0),
          COALESCE(SUM(autoresp_helpful),0),
          COALESCE(SUM(spam_actions),0),
          COALESCE(SUM(night_deletes),0)
        FROM agg_group_day
        WHERE chat_id=%s AND stat_date BETWEEN %s AND %s
    """, (chat_id, d_start, d_end))
    (msgs, aus, joins, leaves, kicks, ar_hits, ar_help, spam, night_del) = cur.fetchone()

    cur.execute("""
        SELECT
          PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY delta_ms),
          PERCENTILE_DISC(0.9) WITHIN GROUP (ORDER BY delta_ms)
        FROM reply_times
        WHERE chat_id=%s AND DATE(ts) BETWEEN %s AND %s
    """, (chat_id, d_start, d_end))
    p50, p90 = cur.fetchone() or (None, None)

    return {
        "messages_total":   int(msgs or 0),
        "active_users":     int(aus or 0),
        "joins":            int(joins or 0),
        "leaves":           int(leaves or 0),
        "kicks":            int(kicks or 0),
        "reply_median_ms":  int(p50) if p50 is not None else None,
        "reply_p90_ms":     int(p90) if p90 is not None else None,
        "autoresp_hits":    int(ar_hits or 0),
        "autoresp_helpful": int(ar_help or 0),
        "spam_actions":     int(spam or 0),
        "night_deletes":    int(night_del or 0),
    }

@_with_cursor
def get_heatmap(cur, chat_id:int, ts_start, ts_end):
    # 0=Sonntag in PostgreSQL => wir mappen auf 1=Mo ... 7=So
    cur.execute("""
        SELECT ((EXTRACT(DOW FROM timestamp)::INT + 6) % 7) + 1 AS dow,  -- 1..7 (Mo..So)
               EXTRACT(HOUR FROM timestamp)::INT AS hour,
               COUNT(*) AS cnt
        FROM message_logs
        WHERE chat_id=%s AND timestamp >= %s AND timestamp < %s
        GROUP BY 1,2
    """, (chat_id, ts_start, ts_end))
    rows = cur.fetchall() or []
    # in Python zu einem 7x24-Grid formen
    grid = [[0]*24 for _ in range(7)]  # [1..7]=Mo..So -> index 0..6
    for dow, hour, cnt in rows:
        grid[dow-1][hour] = int(cnt)
    return grid

@_with_cursor
def get_agg_rows(cur, chat_id: int, d_start, d_end):
    cur.execute("""
        SELECT stat_date, messages_total, active_users, joins, leaves, kicks,
               reply_median_ms, reply_p90_ms, autoresp_hits, autoresp_helpful,
               spam_actions, night_deletes
        FROM agg_group_day
        WHERE chat_id=%s AND stat_date BETWEEN %s AND %s
        ORDER BY stat_date;
    """, (chat_id, d_start, d_end))
    return cur.fetchall()

@_with_cursor
def get_top_responders(cur, chat_id: int, d_start, d_end, limit: int = 10):
    cur.execute("""
        SELECT answer_user,
               COUNT(*)              AS answers,
               AVG(delta_ms)::BIGINT AS avg_ms
        FROM reply_times
        WHERE chat_id=%s AND DATE(ts) BETWEEN %s AND %s
        GROUP BY answer_user
        ORDER BY answers DESC, avg_ms ASC
        LIMIT %s;
    """, (chat_id, d_start, d_end, limit))
    rows = cur.fetchall() or []
    return [(int(u), int(c), int(a)) for (u, c, a) in rows]

@_with_cursor
def ensure_spam_topic_schema(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spam_policy_topic (
          chat_id BIGINT,
          topic_id BIGINT,
          level TEXT,                     -- 'off'|'light'|'medium'|'strict'
          link_whitelist TEXT[],          -- Domains, die immer erlaubt sind
          domain_blacklist TEXT[],        -- Domains, die immer geblockt werden
          emoji_max_per_msg INT,          -- 0 = kein Limit
          emoji_max_per_min INT,          -- 0 = kein Limit
          max_msgs_per_10s INT,           -- Flood-Guard pro User
          action_primary TEXT,            -- 'delete'|'warn'|'mute'
          action_secondary TEXT,          -- 'none'|'mute'|'ban'
          escalation_threshold INT,       -- ab wievielen Treffern eskalieren
          PRIMARY KEY(chat_id, topic_id)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS topic_router_rules (
          rule_id BIGSERIAL PRIMARY KEY,
          chat_id BIGINT NOT NULL,
          target_topic_id BIGINT NOT NULL,
          enabled BOOLEAN DEFAULT TRUE,
          delete_original BOOLEAN DEFAULT TRUE,
          warn_user BOOLEAN DEFAULT TRUE,
          keywords TEXT[],                -- irgendeins matcht → Route
          domains  TEXT[]                 -- Domain-Matches → Route
        );
    """)

def _default_policy():
    return {
        "level": "off",
        "link_whitelist": [],
        "domain_blacklist": [],
        "emoji_max_per_msg": 0,
        "emoji_max_per_min": 0,
        "max_msgs_per_10s": 0,
        "action_primary": "delete",
        "action_secondary": "none",
        "escalation_threshold": 3
    }

_LEVEL_PRESETS = {
    "off":    {},
    "light":  {"emoji_max_per_msg": 20, "max_msgs_per_10s": 10},
    "medium": {"emoji_max_per_msg": 10, "emoji_max_per_min": 60, "max_msgs_per_10s": 6},
    "strict": {"emoji_max_per_msg": 6, "emoji_max_per_min": 30, "max_msgs_per_10s": 4}
}

@_with_cursor
def set_spam_policy_topic(cur, chat_id:int, topic_id:int, **fields):
    # Nur die Felder updaten, die mitgegeben werden
    cols, vals = [], []
    for k, v in fields.items():
        if k in _default_policy():
            cols.append(f"{k} = %s"); vals.append(v)
    if not cols:
        return
    cur.execute("""
        INSERT INTO spam_policy_topic (chat_id, topic_id, {cols})
        VALUES (%s, %s, {vals})
        ON CONFLICT (chat_id, topic_id) DO UPDATE SET {updates};
    """.format(
        cols=", ".join(cols),
        vals=", ".join(["%s"]*len(cols)),
        updates=", ".join([f"{c.split('=')[0]} = EXCLUDED.{c.split('=')[0]}" for c in cols])
    ), (chat_id, topic_id, *vals))

@_with_cursor
def get_spam_policy_topic(cur, chat_id:int, topic_id:int) -> dict|None:
    cur.execute("""
        SELECT level, link_whitelist, domain_blacklist, emoji_max_per_msg, emoji_max_per_min,
               max_msgs_per_10s, action_primary, action_secondary, escalation_threshold
          FROM spam_policy_topic WHERE chat_id=%s AND topic_id=%s;
    """, (chat_id, topic_id))
    row = cur.fetchone()
    if not row: return None
    keys = list(_default_policy().keys())
    return {k: row[i] for i, k in enumerate(keys)}

@_with_cursor
def delete_spam_policy_topic(cur, chat_id:int, topic_id:int):
    cur.execute("DELETE FROM spam_policy_topic WHERE chat_id=%s AND topic_id=%s;", (chat_id, topic_id))

def effective_spam_policy(chat_id:int, topic_id:int|None, link_settings:tuple) -> dict:
    """
    link_settings = (link_protection_enabled, link_warning_enabled, link_warning_text, link_exceptions_enabled)
    Wir leiten daraus das Basis-Level ab und mergen Topic-Overrides.
    """
    prot_on, warn_on, warn_text, except_on = link_settings
    base = _default_policy()
    base["level"] = "strict" if prot_on else "off"
    # Preset reinmischen:
    base.update(_LEVEL_PRESETS.get(base["level"], {}))
    # Topic-Override mergen
    if topic_id:
        ov = get_spam_policy_topic(chat_id, topic_id)
        if ov:
            for k, v in ov.items():
                if v is not None:
                    base[k] = v
            # Level-Preset des Overrides auch anwenden:
            base.update(_LEVEL_PRESETS.get(base["level"], {}))
    return base

# --- Topic Router ---
@_with_cursor
def add_topic_router_rule(cur, chat_id:int, target_topic_id:int,
                          keywords:list[str]|None=None,
                          domains:list[str]|None=None,
                          delete_original:bool=True,
                          warn_user:bool=True) -> int:
    cur.execute("""
        INSERT INTO topic_router_rules
          (chat_id, target_topic_id, keywords, domains, delete_original, warn_user)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING rule_id;
    """, (chat_id, target_topic_id, keywords or [], domains or [], delete_original, warn_user))
    return cur.fetchone()[0]

@_with_cursor
def list_topic_router_rules(cur, chat_id:int):
    cur.execute("""
        SELECT rule_id, target_topic_id, enabled, delete_original, warn_user, keywords, domains
          FROM topic_router_rules
         WHERE chat_id=%s
         ORDER BY rule_id ASC;
    """, (chat_id,))
    return cur.fetchall()

@_with_cursor
def delete_topic_router_rule(cur, chat_id:int, rule_id:int):
    cur.execute("DELETE FROM topic_router_rules WHERE chat_id=%s AND rule_id=%s;", (chat_id, rule_id))

@_with_cursor
def toggle_topic_router_rule(cur, chat_id:int, rule_id:int, enabled:bool):
    cur.execute("UPDATE topic_router_rules SET enabled=%s WHERE chat_id=%s AND rule_id=%s;",
                (enabled, chat_id, rule_id))

@_with_cursor
def get_matching_router_rule(cur, chat_id:int, text:str, domains_in_msg:list[str]):
    cur.execute("""
        SELECT rule_id, target_topic_id, delete_original, warn_user, keywords, domains
          FROM topic_router_rules
         WHERE chat_id=%s AND enabled=TRUE
         ORDER BY rule_id ASC;
    """, (chat_id,))
    domains_norm = {d.lower() for d in (domains_in_msg or [])}
    for rule_id, target_topic_id, del_orig, warn_user, kws, doms in cur.fetchall():
        kws  = kws or []
        doms = doms or []
        kw_hit  = any(kw.lower() in text.lower() for kw in kws) if kws else False
        dom_hit = any((d or "").lower() in domains_norm for d in doms) if doms else False
        if (kws and kw_hit) or (doms and dom_hit):
            return {"rule_id": rule_id, "target_topic_id": target_topic_id,
                    "delete_original": del_orig, "warn_user": warn_user}
    return None

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
def set_mood_topic(cur, chat_id: int, topic_id: int) -> None:
    """Set the default topic ID for mood questions in a chat"""
    cur.execute(
        """
        INSERT INTO group_settings (chat_id, mood_topic_id)
        VALUES (%s, %s)
        ON CONFLICT (chat_id) 
        DO UPDATE SET mood_topic_id = EXCLUDED.mood_topic_id
        """,
        (chat_id, topic_id)
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

@_with_cursor
def get_captcha_settings(cur, chat_id: int):
    cur.execute(
        "SELECT captcha_enabled, captcha_type, captcha_behavior FROM group_settings WHERE chat_id=%s",
        (chat_id,)
    )
    return cur.fetchone() or (False, 'button', 'kick')

@_with_cursor
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
def set_rss_feed_options(cur, chat_id:int, url:str, *, post_images:bool|None=None, enabled:bool|None=None):
    parts, params = [], []
    if post_images is not None:
        parts.append("post_images=%s"); params.append(post_images)
    if enabled is not None:
        parts.append("enabled=%s"); params.append(enabled)
    if not parts: return
    sql = "UPDATE rss_feeds SET " + ", ".join(parts) + " WHERE chat_id=%s AND url=%s;"
    cur.execute(sql, params + [chat_id, url])

@_with_cursor
def update_rss_http_cache(cur, chat_id:int, url:str, etag:str|None, last_modified:str|None):
    cur.execute("""
        UPDATE rss_feeds SET last_etag=%s, last_modified=%s
        WHERE chat_id=%s AND url=%s;
    """, (etag, last_modified, chat_id, url))

@_with_cursor
def get_rss_feeds_full(cur):
    cur.execute("""
        SELECT chat_id, url, topic_id, last_etag, last_modified, post_images, enabled
        FROM rss_feeds
        ORDER BY chat_id, url;
    """)
    return cur.fetchall()

@_with_cursor
def get_ai_settings(cur, chat_id:int) -> tuple[bool,bool]:
    cur.execute("SELECT ai_faq_enabled, ai_rss_summary FROM group_settings WHERE chat_id=%s;", (chat_id,))
    row = cur.fetchone()
    return (row[0], row[1]) if row else (False, False)

@_with_cursor
def set_ai_settings(cur, chat_id:int, faq:bool|None=None, rss:bool|None=None):
    parts, params = [], []
    if faq is not None: parts.append("ai_faq_enabled=%s"); params.append(faq)
    if rss is not None: parts.append("ai_rss_summary=%s"); params.append(rss)
    if not parts: return
    sql = "INSERT INTO group_settings(chat_id) VALUES (%s) ON CONFLICT (chat_id) DO UPDATE SET " + ", ".join(parts)
    cur.execute(sql, [chat_id] + params)

@_with_cursor
def upsert_faq(cur, chat_id:int, trigger:str, answer:str):
    cur.execute("""
        INSERT INTO faq_snippets (chat_id, trigger, answer)
        VALUES (%s, %s, %s)
        ON CONFLICT (chat_id, trigger) DO UPDATE SET answer=EXCLUDED.answer;
    """, (chat_id, trigger.strip(), answer.strip()))

@_with_cursor
def list_faqs(cur, chat_id:int):
    cur.execute("SELECT trigger, answer FROM faq_snippets WHERE chat_id=%s ORDER BY trigger ASC;", (chat_id,))
    return cur.fetchall()

@_with_cursor
def delete_faq(cur, chat_id:int, trigger:str):
    cur.execute("DELETE FROM faq_snippets WHERE chat_id=%s AND trigger=%s;", (chat_id, trigger))

@_with_cursor
def find_faq_answer(cur, chat_id:int, text:str) -> tuple[str,str]|None:
    # sehr einfache Heuristik: Trigger als Substring (case-insensitive)
    cur.execute("""
      SELECT trigger, answer
        FROM faq_snippets
       WHERE chat_id=%s AND LOWER(%s) LIKE CONCAT('%%', LOWER(trigger), '%%')
       ORDER BY LENGTH(trigger) DESC
       LIMIT 1;
    """, (chat_id, text))
    return cur.fetchone()

@_with_cursor
def log_auto_response(cur, chat_id:int, trigger:str, matched:float, snippet:str, latency_ms:int, was_helpful:bool|None=None):
    cur.execute("""
      INSERT INTO auto_responses (chat_id, trigger, matched_confidence, used_snippet, latency_ms, ts, was_helpful)
      VALUES (%s,%s,%s,%s,%s,NOW(),%s);
    """, (chat_id, trigger, matched, snippet, latency_ms, was_helpful))

@_with_cursor
def get_posted_links(cur, chat_id: int) -> list:
    cur.execute("SELECT link FROM last_posts WHERE chat_id = %s ORDER BY posted_at DESC;", (chat_id,))
    return [row[0] for row in cur.fetchall()]

@_with_cursor
def add_posted_link(cur, chat_id: int, link: str, feed_url: Optional[str] = None):
    """
    Speichert den letzten geposteten Link. 
    - Wenn feed_url gegeben: pro Feed deduplizieren.
    - Ohne feed_url: Fallback in einen 'single-slot' (kompatibel zu Alt-Aufrufen).
    """
    if feed_url is None:
        feed_url = "__single__"  # stabiler Fallback-Key pro Chat
    cur.execute("""
        INSERT INTO last_posts (chat_id, feed_url, link, posted_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (chat_id, feed_url)
        DO UPDATE SET link = EXCLUDED.link, posted_at = EXCLUDED.posted_at;
    """, (chat_id, feed_url, link))

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
    conn = _db_pool.getconn()
    try:
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
        conn.commit()
    finally:
        _db_pool.putconn(conn)

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

# --- Night Mode Settings ---
@_with_cursor
def get_night_mode(cur, chat_id: int):
    cur.execute("""
      SELECT enabled, start_minute, end_minute, delete_non_admin_msgs, warn_once, timezone, 
             COALESCE(hard_mode, FALSE), override_until
        FROM night_mode WHERE chat_id = %s;
    """, (chat_id,))
    row = cur.fetchone()
    if not row:
        # Defaults wie im Schema
        return (False, 1320, 360, True, True, 'Europe/Berlin', False, None)
    return row

@_with_cursor
def set_night_mode(cur, chat_id: int,
                   enabled=None,
                   start_minute=None,
                   end_minute=None,
                   delete_non_admin_msgs=None,
                   warn_once=None,
                   timezone=None,
                   hard_mode=None,
                   override_until=None):
    parts, params = [], []
    if enabled is not None: parts.append("enabled=%s"); params.append(enabled)
    if start_minute is not None: parts.append("start_minute=%s"); params.append(start_minute)
    if end_minute is not None: parts.append("end_minute=%s"); params.append(end_minute)
    if delete_non_admin_msgs is not None: parts.append("delete_non_admin_msgs=%s"); params.append(delete_non_admin_msgs)
    if warn_once is not None: parts.append("warn_once=%s"); params.append(warn_once)
    if timezone is not None: parts.append("timezone=%s"); params.append(timezone)
    if hard_mode is not None: parts.append("hard_mode=%s"); params.append(hard_mode)
    if override_until is not None: parts.append("override_until=%s"); params.append(override_until)

    if not parts:
        return
    sql = "INSERT INTO night_mode (chat_id) VALUES (%s) ON CONFLICT (chat_id) DO UPDATE SET " + ", ".join(parts)
    cur.execute(sql, [chat_id] + params)
    
# --- Legacy Migration Utility ---
def migrate_db():
    import psycopg2
    logging.basicConfig(level=logging.INFO)
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    try:
        logging.info("Starte Migration für bestehende Tabellen...")
        # FIX FOR reply_times TABLE
        try:
            cur.execute("ALTER TABLE reply_times ADD COLUMN IF NOT EXISTS chat_id BIGINT;")
        except psycopg2.Error as e:
            logging.warning(f"Could not alter reply_times, might not exist yet: {e}")
            conn.rollback() # Rollback this specific transaction
        
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
        cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS captcha_behavior TEXT NOT NULL DEFAULT 'kick';")
        
        cur.execute("""
        ALTER TABLE group_settings
        ADD COLUMN IF NOT EXISTS link_protection_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS link_warning_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS link_warning_text       TEXT    NOT NULL DEFAULT '⚠️ Nur Admins dürfen Links posten.',
        ADD COLUMN IF NOT EXISTS link_exceptions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        ADD COLUMN IF NOT EXISTS mood_topic_id BIGINT NOT NULL DEFAULT 0;
        """)
        
        cur.execute("""
            ALTER TABLE night_mode
            ADD COLUMN IF NOT EXISTS hard_mode BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS override_until TIMESTAMPTZ NULL;
        """)
        
        cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS ai_faq_enabled BOOLEAN NOT NULL DEFAULT FALSE;")
        cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS ai_rss_summary BOOLEAN NOT NULL DEFAULT FALSE;")
        cur.execute("ALTER TABLE auto_responses ADD COLUMN IF NOT EXISTS was_helpful BOOLEAN;")
        
            # ---- Indizes jetzt sicher anlegen ----
        cur.execute("CREATE INDEX IF NOT EXISTS idx_message_logs_chat_ts ON message_logs(chat_id, timestamp DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_member_events_chat_ts ON member_events(chat_id, ts DESC);")
        
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
def init_all_schemas():
    """Initialize all database schemas and ensure migrations are applied"""
    logger.info("Initializing all database schemas...")
    init_db()
    migrate_db()
    migrate_stats_rollup()
    ensure_spam_topic_schema()
    ensure_forum_topics_schema()
    logger.info("✅ All schemas initialized successfully")

if __name__ == "__main__":
    init_all_schemas()
