# Cache für Spaltenerkennung in pending_inputs
_pi_col_cache: str | None = None
import re
import os
import json
import logging
from urllib.parse import urlparse
from datetime import date
from typing import List, Dict, Tuple, Optional, Any
from psycopg2 import pool, OperationalError, InterfaceError
from psycopg2.extras import Json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Logger setup
logger = logging.getLogger(__name__)

DEFAULT_BOT_KEY = os.getenv("BOT1_KEY", "content")

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
        # bis zu 2 Versuche bei transienten Verbindungsproblemen
        import time
        last_exc = None
        for attempt in (1, 2):
            conn = _db_pool.getconn()
            try:
                # Verbindung „gesund“? (sofortiger Ping)
                if getattr(conn, "closed", 0):
                    raise OperationalError("connection closed")
                with conn.cursor() as cur_ping:
                    cur_ping.execute("SELECT 1;")
                # eigentlicher DB-Call
                with conn.cursor() as cur:
                    logger.debug(f"[DB] Calling {func.__name__} args={args} kwargs={kwargs}")
                    res = func(cur, *args, **kwargs)
                    conn.commit()
                    return res
            except (OperationalError, InterfaceError) as e:
                last_exc = e
                logger.error(f"[DB] Operational/Interface error in {func.__name__}: {e}")
                # defekte Verbindung hart schließen und aus dem Pool entfernen
                try:
                    conn.close()
                except Exception:
                    pass
                try:
                    _db_pool.putconn(conn, close=True)
                except TypeError:
                    # ältere psycopg2 ohne close-Flag
                    try: _db_pool.putconn(conn)
                    except Exception: pass
                if attempt == 2:
                    raise
                # kurzer Backoff, dann neuer Versuch
                time.sleep(0.2)
                continue
            except Exception as e:
                logger.error(f"[DB] Exception in {func.__name__}: {e}", exc_info=True)
                raise
            finally:
                try:
                    if conn and not getattr(conn, "closed", 0):
                        _db_pool.putconn(conn)
                except Exception:
                    pass
    return wrapped

async def _call_db_safe(fn, *args, **kwargs):
    """
    Führt eine (synchrone) DB-Funktion sicher aus, loggt Exceptions
    und lässt sie nach oben steigen, damit der Aufrufer reagieren kann.
    Absichtlich 'async', damit bestehende Aufrufe mit 'await' unverändert bleiben.
    """
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.exception("DB-Fehler in %s", getattr(fn, "__name__", str(fn)))
        raise

def load_group_settings(cur, chat_id, bot_key: str):
    cur.execute("""
        SELECT setting_key, setting_value
        FROM settings
        WHERE bot_key=%s AND chat_id=%s
    """, (bot_key, chat_id))
    return dict(cur.fetchall())

def get_bot_key_from_context(context) -> str:
    """
    Holt den bot_key aus der PTB Application,
    fällt auf DEFAULT_BOT_KEY zurück, wenn nicht vorhanden.
    """
    try:
        return context.application.bot_data.get('bot_key') or DEFAULT_BOT_KEY
    except Exception:
        return DEFAULT_BOT_KEY

def qualify_where(query_base: str) -> str:
    """
    Hilfsfunktion: hängt 'WHERE bot_key=%s AND ...' oder
    '... AND bot_key=%s' sinnvoll an – je nachdem, ob schon ein WHERE existiert.
    Nutze das optional beim Refactoring.
    """
    q = query_base.strip()
    if " where " in q.lower():
        return q + " AND bot_key = %s"
    return q + " WHERE bot_key = %s"

def ensure_bot_key_param(params: tuple, bot_key: str) -> tuple:
    """
    Hack-freie Art, am Ende den bot_key als zusätzliches %s anzuhängen,
    wenn du qualify_where() genutzt hast.
    """
    return (*params, bot_key)

def _alter_table_safe(cur, sql: str):
    """
    Führt ein ALTER/CREATE sicher aus.
    - Ignoriert fehlende Tabellen (Relation does not exist)
    - Loggt andere Fehler nur als Warning
    """
    try:
        cur.execute(sql)
    except Exception as e:
        msg = str(e).lower()
        if "does not exist" in msg or "relation" in msg and "does not exist" in msg:
            logger.debug("Schema-Skip (Tabelle fehlt): %s", sql)
        else:
            logger.warning("Schema-Änderung fehlgeschlagen: %s  --  %s", sql, e)

@_with_cursor
def ensure_multi_bot_schema(cur):
    """
    Minimale Erweiterung für Multi-Bot: fügt `bot_key` + sinnvolle Indizes
    auf stark genutzten Tabellen hinzu. Idempotent & sicher.
    Nur Tabellen, die es bei dir gibt, werden verändert.
    """
    
    stmts = [
        # Kern: Gruppen & Settings
        "ALTER TABLE groups          ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_groups_bot_chat ON groups (bot_key, chat_id)",
        "ALTER TABLE group_settings  ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_group_settings_bot_chat ON group_settings (bot_key, chat_id)",

        # Logs & Statistiken
        "ALTER TABLE message_logs    ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE INDEX IF NOT EXISTS ix_message_logs_bot_chat_ts ON message_logs (bot_key, chat_id, timestamp DESC)",
        "ALTER TABLE daily_stats     ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_daily_stats_bot_chat_date_user ON daily_stats (bot_key, chat_id, stat_date, user_id)",
        "ALTER TABLE reply_times     ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE INDEX IF NOT EXISTS ix_reply_times_bot_chat_ts ON reply_times (bot_key, chat_id, ts DESC)",
        "ALTER TABLE auto_responses  ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE INDEX IF NOT EXISTS ix_auto_responses_bot_chat_ts ON auto_responses (bot_key, chat_id, ts DESC)",

        # RSS & zuletzt gepostet
        "ALTER TABLE rss_feeds       ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_rss_feeds_bot_chat_url ON rss_feeds (bot_key, chat_id, url)",
        "ALTER TABLE last_posts      ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_last_posts_bot_chat_feed ON last_posts (bot_key, chat_id, feed_url)",

        # Mitglieder / Topics
        "ALTER TABLE members         ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE INDEX IF NOT EXISTS ix_members_bot_chat ON members (bot_key, chat_id)",
        "ALTER TABLE forum_topics    ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_forum_topics_bot_chat_topic ON forum_topics (bot_key, chat_id, topic_id)",
        "ALTER TABLE topic_router_rules ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE INDEX IF NOT EXISTS ix_topic_router_bot_chat ON topic_router_rules (bot_key, chat_id)",

        # Ads / Subscriptions
        "ALTER TABLE adv_settings    ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_adv_settings_bot_chat ON adv_settings (bot_key, chat_id)",
        "ALTER TABLE adv_campaigns   ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "ALTER TABLE adv_impressions ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE INDEX IF NOT EXISTS ix_adv_impr_bot_chat_ts ON adv_impressions (bot_key, chat_id, ts DESC)",
        "ALTER TABLE group_subscriptions ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_group_subs_bot_chat ON group_subscriptions (bot_key, chat_id)",
        "ALTER TABLE mood_topics     ADD COLUMN IF NOT EXISTS bot_key text NOT NULL DEFAULT 'content'",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_mood_topics_bot_chat ON mood_topics (bot_key, chat_id)",
    ]
    for sql in stmts:
        _alter_table_safe(cur, sql)

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
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS adv_settings (
          chat_id           BIGINT PRIMARY KEY,
          adv_enabled       BOOLEAN NOT NULL DEFAULT TRUE,
          adv_topic_id      BIGINT NULL,             -- wenn NULL => Default-Topic
          min_gap_min       INT     NOT NULL DEFAULT 240,  -- Mindestabstand in Minuten
          daily_cap         INT     NOT NULL DEFAULT 2,    -- max Ads/Tag
          every_n_messages  INT     NOT NULL DEFAULT 0,    -- optional: nach N Nachrichten
          label             TEXT    NOT NULL DEFAULT 'Anzeige',
          quiet_start_min   SMALLINT NOT NULL DEFAULT 1320, -- 22*60
          quiet_end_min     SMALLINT NOT NULL DEFAULT 360,  -- 06*60
          last_adv_ts       TIMESTAMPTZ NULL
        );
    """)

    # Kampagnen – global (Targeting optional später)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS adv_campaigns (
          campaign_id   BIGSERIAL PRIMARY KEY,
          title         TEXT,
          body_text     TEXT,
          media_url     TEXT,     -- optional Bild
          link_url      TEXT,     -- URL für CTA
          cta_label     TEXT DEFAULT 'Mehr erfahren',
          enabled       BOOLEAN NOT NULL DEFAULT TRUE,
          weight        INT NOT NULL DEFAULT 1,
          start_ts      TIMESTAMPTZ NULL,
          end_ts        TIMESTAMPTZ NULL,
          created_by    BIGINT,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # Impressionen/Versände
    cur.execute("""
        CREATE TABLE IF NOT EXISTS adv_impressions (
          chat_id     BIGINT,
          campaign_id BIGINT,
          message_id  BIGINT,
          ts          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_adv_impr_chat_ts ON adv_impressions(chat_id, ts DESC);")

    # Hilfsindex für Message-basierte Trigger
    cur.execute("ALTER TABLE message_logs ADD COLUMN IF NOT EXISTS topic_id BIGINT;")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_msglogs_topic_user_ts ON message_logs(chat_id, topic_id, user_id, timestamp DESC);")
    
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
    
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS group_subscriptions (
          chat_id     BIGINT PRIMARY KEY,
          tier        TEXT NOT NULL DEFAULT 'free',
          valid_until TIMESTAMPTZ,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mood_topics (
            chat_id BIGINT PRIMARY KEY,
            topic_id BIGINT
        );
        """
    )
    
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_inputs (
            ctx_chat_id BIGINT NOT NULL,
            user_id     BIGINT NOT NULL,
            key         TEXT   NOT NULL,
            payload     JSONB,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (ctx_chat_id, user_id, key)
        );
    """)

    _pending_inputs_col(cur)

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

# --- KI Management ---

@_with_cursor
def ensure_ai_moderation_schema(cur):
    # Settings (global=topic_id=0; overrides pro Topic möglich)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ai_mod_settings (
          chat_id            BIGINT,
          topic_id           BIGINT,
          enabled            BOOLEAN   NOT NULL DEFAULT FALSE,
          shadow_mode        BOOLEAN   NOT NULL DEFAULT TRUE,        -- nur loggen, nicht handeln
          model              TEXT      NOT NULL DEFAULT 'omni-moderation-latest',
          lang               TEXT      NOT NULL DEFAULT 'de',
          tox_thresh         REAL      NOT NULL DEFAULT 0.90,
          hate_thresh        REAL      NOT NULL DEFAULT 0.85,
          sex_thresh         REAL      NOT NULL DEFAULT 0.90,
          harass_thresh      REAL      NOT NULL DEFAULT 0.90,
          selfharm_thresh    REAL      NOT NULL DEFAULT 0.95,
          violence_thresh    REAL      NOT NULL DEFAULT 0.90,
          link_risk_thresh   REAL      NOT NULL DEFAULT 0.95,
          action_primary     TEXT      NOT NULL DEFAULT 'delete',    -- delete|warn|mute|ban
          action_secondary   TEXT      NOT NULL DEFAULT 'warn',
          escalate_after     INT       NOT NULL DEFAULT 3,           -- nach X Treffern eskalieren
          escalate_action    TEXT      NOT NULL DEFAULT 'mute',      -- mute|ban
          mute_minutes       INT       NOT NULL DEFAULT 60,
          exempt_admins      BOOLEAN   NOT NULL DEFAULT TRUE,
          exempt_topic_owner BOOLEAN   NOT NULL DEFAULT TRUE,
          max_calls_per_min  INT       NOT NULL DEFAULT 20,
          cooldown_s         INT       NOT NULL DEFAULT 30,
          warn_text          TEXT      NOT NULL DEFAULT '⚠️ Inhalt entfernt (KI-Moderation).',
          appeal_url         TEXT,
          PRIMARY KEY (chat_id, topic_id)
        );
    """)
    # Logs
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ai_mod_logs (
          chat_id     BIGINT,
          topic_id    BIGINT,
          user_id     BIGINT,
          message_id  BIGINT,
          category    TEXT,
          score       REAL,
          action      TEXT,
          details     JSONB,
          ts          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    cur.execute("ALTER TABLE ai_mod_settings ADD COLUMN IF NOT EXISTS visual_nudity_thresh   REAL NOT NULL DEFAULT 0.90;")
    cur.execute("ALTER TABLE ai_mod_settings ADD COLUMN IF NOT EXISTS visual_violence_thresh REAL NOT NULL DEFAULT 0.90;")
    cur.execute("ALTER TABLE ai_mod_settings ADD COLUMN IF NOT EXISTS visual_weapons_thresh  REAL NOT NULL DEFAULT 0.95;")
    cur.execute("ALTER TABLE ai_mod_settings ADD COLUMN IF NOT EXISTS block_sexual_minors    BOOLEAN NOT NULL DEFAULT TRUE;")
    cur.execute("ALTER TABLE ai_mod_settings ADD COLUMN IF NOT EXISTS strike_points_per_hit  INT NOT NULL DEFAULT 1;")
    cur.execute("ALTER TABLE ai_mod_settings ADD COLUMN IF NOT EXISTS strike_mute_threshold  INT NOT NULL DEFAULT 3;")
    cur.execute("ALTER TABLE ai_mod_settings ADD COLUMN IF NOT EXISTS strike_ban_threshold   INT NOT NULL DEFAULT 5;")
    cur.execute("ALTER TABLE ai_mod_settings ADD COLUMN IF NOT EXISTS strike_decay_days      INT NOT NULL DEFAULT 30;")

    # Logs existieren: ai_mod_logs – ok.

    # Strike-Speicher
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_strikes (
          chat_id   BIGINT,
          user_id   BIGINT,
          points    INT NOT NULL DEFAULT 0,
          updated   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (chat_id, user_id)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_strike_events (
          chat_id   BIGINT,
          user_id   BIGINT,
          points    INT NOT NULL,
          reason    TEXT,
          ts        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    
    # Sinnvolle Zusatzindizes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_strike_ev_chat_ts ON user_strike_events(chat_id, ts DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_mod_logs_chat_ts ON ai_mod_logs(chat_id, ts DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_mod_logs_user_day ON ai_mod_logs(chat_id, user_id, ts DESC);")

@_with_cursor
def set_ai_mod_settings(cur, chat_id:int, topic_id:int, **fields):
    allowed = {
        "enabled","shadow_mode","model","lang",
        "tox_thresh","hate_thresh","sex_thresh","harass_thresh","selfharm_thresh","violence_thresh",
        "link_risk_thresh","action_primary","action_secondary",
        "escalate_after","escalate_action","mute_minutes",
        "exempt_admins","exempt_topic_owner","max_calls_per_min","cooldown_s",
        "warn_text","appeal_url"
    }
    cols, vals = [], []
    for k,v in fields.items():
        if k in allowed:
            cols.append(f"{k}=%s"); vals.append(v)
    if not cols: return
    cur.execute(f"""
      INSERT INTO ai_mod_settings (chat_id, topic_id) VALUES (%s,%s)
      ON CONFLICT (chat_id, topic_id) DO UPDATE SET {", ".join(cols)};
    """, (chat_id, topic_id, *vals))

@_with_cursor
def get_ai_mod_settings(cur, chat_id:int, topic_id:int) -> dict|None:
    cur.execute("""
      SELECT enabled, shadow_mode, model, lang,
             tox_thresh, hate_thresh, sex_thresh, harass_thresh, selfharm_thresh, violence_thresh,
             link_risk_thresh, action_primary, action_secondary, escalate_after, escalate_action,
             mute_minutes, exempt_admins, exempt_topic_owner, max_calls_per_min, cooldown_s,
             warn_text, appeal_url,
             visual_nudity_thresh, visual_violence_thresh, visual_weapons_thresh, block_sexual_minors,
             strike_points_per_hit, strike_mute_threshold, strike_ban_threshold, strike_decay_days
        FROM ai_mod_settings WHERE chat_id=%s AND topic_id=%s;
    """, (chat_id, topic_id))
    r = cur.fetchone()
    if not r: return None
    keys = ["enabled","shadow_mode","model","lang",
            "tox_thresh","hate_thresh","sex_thresh","harass_thresh","selfharm_thresh","violence_thresh",
            "link_risk_thresh","action_primary","action_secondary","escalate_after","escalate_action",
            "mute_minutes","exempt_admins","exempt_topic_owner","max_calls_per_min","cooldown_s",
            "warn_text","appeal_url",
            "visual_nudity_thresh","visual_violence_thresh","visual_weapons_thresh","block_sexual_minors",
            "strike_points_per_hit","strike_mute_threshold","strike_ban_threshold","strike_decay_days"]
    return {k: r[i] for i,k in enumerate(keys)}

def effective_ai_mod_policy(chat_id:int, topic_id:int|None) -> dict:
    base = get_ai_mod_settings(chat_id, 0) or {
        "enabled": False, "shadow_mode": True, "model":"omni-moderation-latest", "lang":"de",
        "tox_thresh":0.90,"hate_thresh":0.85,"sex_thresh":0.90,"harass_thresh":0.90,"selfharm_thresh":0.95,"violence_thresh":0.90,
        "link_risk_thresh":0.95, "action_primary":"delete","action_secondary":"warn",
        "escalate_after":3,"escalate_action":"mute","mute_minutes":60,"exempt_admins":True,"exempt_topic_owner":True,
        "max_calls_per_min":20,"cooldown_s":30,"warn_text":"⚠️ Inhalt entfernt (KI-Moderation).","appeal_url":None,
        "visual_nudity_thresh":0.90,"visual_violence_thresh":0.90,"visual_weapons_thresh":0.95,"block_sexual_minors":True,
        "strike_points_per_hit":1,"strike_mute_threshold":3,"strike_ban_threshold":5,"strike_decay_days":30,
    }
    if topic_id:
        ov = get_ai_mod_settings(chat_id, topic_id)
        if ov: base.update({k:v for k,v in ov.items() if v is not None})
    return base

@_with_cursor
def add_strike_points(cur, chat_id:int, user_id:int, points:int, reason:str):
    cur.execute("""
      INSERT INTO user_strikes (chat_id, user_id, points, updated)
      VALUES (%s,%s,%s,NOW())
      ON CONFLICT (chat_id,user_id) DO UPDATE SET points=user_strikes.points+EXCLUDED.points, updated=NOW();
    """, (chat_id, user_id, points))
    cur.execute("INSERT INTO user_strike_events (chat_id,user_id,points,reason) VALUES (%s,%s,%s,%s);",
                (chat_id, user_id, points, reason))

@_with_cursor
def get_strike_points(cur, chat_id:int, user_id:int) -> int:
    cur.execute("SELECT points FROM user_strikes WHERE chat_id=%s AND user_id=%s;", (chat_id, user_id))
    r = cur.fetchone()
    return int(r[0]) if r else 0

@_with_cursor
def decay_strikes(cur, chat_id:int, days:int):
    cur.execute("UPDATE user_strikes SET points=GREATEST(points-1,0), updated=NOW() WHERE chat_id=%s AND updated < NOW()-(%s||' days')::interval;",
                (chat_id, days))

@_with_cursor
def top_strike_users(cur, chat_id:int, limit:int=10):
    cur.execute("SELECT user_id, points FROM user_strikes WHERE chat_id=%s ORDER BY points DESC, updated DESC LIMIT %s;", (chat_id, limit))
    return cur.fetchall() or []

@_with_cursor
def log_ai_mod_action(cur, chat_id:int, topic_id:int|None, user_id:int|None, message_id:int|None,
                      category:str, score:float, action:str, details:dict|None):
    cur.execute("""
      INSERT INTO ai_mod_logs (chat_id, topic_id, user_id, message_id, category, score, action, details)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s);
    """, (chat_id, topic_id, user_id, message_id, category, score, action, json.dumps(details or {})))

@_with_cursor
def count_ai_hits_today(cur, chat_id:int, user_id:int) -> int:
    cur.execute("""
      SELECT COUNT(*) FROM ai_mod_logs
       WHERE chat_id=%s AND user_id=%s AND ts::date=NOW()::date AND action IN ('delete','warn','mute','ban')
    """, (chat_id, user_id))
    return int(cur.fetchone()[0])

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

@_with_cursor
def set_pending_input(cur, chat_id: int, user_id: int, key: str, payload: dict | None):
    if payload is None:
        payload = {}
    col = _pending_inputs_col(cur)
    cur.execute(
        f"""
        INSERT INTO pending_inputs ({col}, user_id, key, payload)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT ({col}, user_id, key)
        DO UPDATE SET payload = EXCLUDED.payload, created_at = NOW();
        """,
        (chat_id, user_id, key, Json(payload, dumps=json.dumps))
    )

@_with_cursor
def get_pending_input(cur, chat_id: int, user_id: int, key: str) -> dict | None:
    col = _pending_inputs_col(cur)
    cur.execute(f"SELECT payload FROM pending_inputs WHERE {col}=%s AND user_id=%s AND key=%s;",
                (chat_id, user_id, key))
    row = cur.fetchone()
    return row[0] if row else None

@_with_cursor
def get_pending_inputs(cur, chat_id: int, user_id: int) -> dict[str, dict]:
    col = _pending_inputs_col(cur)
    cur.execute(
        f"SELECT key, payload FROM pending_inputs WHERE {col}=%s AND user_id=%s;",
        (chat_id, user_id)
    )
    rows = cur.fetchall() or []
    out = {}
    for k, p in rows:
        if isinstance(p, dict):
            out[k] = p
        elif isinstance(p, str):
            try:
                import json
                out[k] = json.loads(p) if p else {}
            except Exception:
                out[k] = {}
        else:
            out[k] = {}
    return out  # niemals None

@_with_cursor
def clear_pending_input(cur, chat_id: int, user_id: int, key: str | None = None):
    col = _pending_inputs_col(cur)
    if key:
        cur.execute(f"DELETE FROM pending_inputs WHERE {col}=%s AND user_id=%s AND key=%s;",
                    (chat_id, user_id, key))
    else:
        cur.execute(f"DELETE FROM pending_inputs WHERE {col}=%s AND user_id=%s;",
                    (chat_id, user_id))

@_with_cursor
def prune_pending_inputs_older_than(cur, hours:int=48):
    cur.execute("DELETE FROM pending_inputs WHERE created_at < NOW() - (%s || ' hours')::interval;", (hours,))

@_with_cursor
def get_clean_deleted_settings(cur, chat_id: int) -> dict:
    cur.execute("""
        SELECT clean_deleted_enabled,
               clean_deleted_hh,
               clean_deleted_mm,
               clean_deleted_weekday,
               clean_deleted_demote,      -- <— NICHT *_admins
               clean_deleted_notify
          FROM group_settings
         WHERE chat_id=%s;
    """, (chat_id,))
    row = cur.fetchone()
    if not row:
        return {"enabled": False, "hh": 3, "mm": 0, "weekday": None, "demote": False, "notify": True}
    en, hh, mm, wd, demote, notify = row
    return {
        "enabled": bool(en),
        "hh": hh if hh is not None else 3,
        "mm": mm if mm is not None else 0,
        "weekday": wd,            # None = täglich
        "demote": bool(demote),
        "notify": bool(notify),
    }

@_with_cursor
def set_clean_deleted_settings(cur, chat_id: int, **kw):
    mapping = {
        "enabled": "clean_deleted_enabled",
        "hh":      "clean_deleted_hh",
        "mm":      "clean_deleted_mm",
        "weekday": "clean_deleted_weekday",
        "demote":  "clean_deleted_demote",
        "notify":  "clean_deleted_notify",
    }
    sets, params = [], []
    for k, v in kw.items():
        col = mapping.get(k)
        if col is not None:
            sets.append(f"{col}=%s")
            params.append(v)
    if sets:
        params.append(chat_id)
        cur.execute(f"UPDATE group_settings SET {', '.join(sets)} WHERE chat_id=%s;", params)
        
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
def get_link_settings(cur, chat_id:int):
    cur.execute("""
        SELECT link_protection_enabled, link_warning_enabled, link_warning_text, link_exceptions_enabled
          FROM group_settings WHERE chat_id=%s;
    """, (chat_id,))
    row = cur.fetchone()
    if not row:
        return None
    return {
        "only_admin_links": bool(row[0]),
        "admins_only": bool(row[0]),  # alias
        "warning_enabled": bool(row[1]),
        "warning_text": row[2],
        "exceptions_enabled": bool(row[3]),
    }

@_with_cursor
def set_link_settings(cur, chat_id: int,
                      protection: bool | None = None,
                      warning_on: bool | None = None,
                      warning_text: str | None = None,
                      exceptions_on: bool | None = None,
                      only_admin_links: bool | None = None,   # NEU: Alias
                      admins_only: bool | None = None):        # NEU: weiterer Alias
    logger.info(f"DB: set_link_settings für Chat {chat_id} protection={protection} only_admin_links={only_admin_links} admins_only={admins_only}")
    # Aliase auf 'protection' abbilden (falls gesetzt)
    if protection is None:
        if only_admin_links is not None:
            protection = bool(only_admin_links)
        elif admins_only is not None:
            protection = bool(admins_only)

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
        logger.info("DB: set_link_settings – keine Änderungen")
        return
    sql = "INSERT INTO group_settings(chat_id) VALUES (%s) ON CONFLICT (chat_id) DO UPDATE SET "
    sql += ", ".join(parts)
    params = [chat_id] + params
    logger.info(f"DB: set_link_settings SQL: {sql} PARAMS: {params}")
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
    # 0=Sonntag → 1..7 (Mo..So)
    cur.execute("""
        SELECT (MOD(EXTRACT(DOW FROM "timestamp")::INT + 6, 7) + 1) AS dow,
               EXTRACT(HOUR FROM "timestamp")::INT AS hour,
               COUNT(*) AS cnt
        FROM message_logs
        WHERE chat_id=%s AND "timestamp" >= %s AND "timestamp" < %s
        GROUP BY 1,2
    """, (chat_id, ts_start, ts_end))
    rows = cur.fetchall() or []
    grid = [[0]*24 for _ in range(7)]
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
      level TEXT,
      link_whitelist TEXT[],
      domain_blacklist TEXT[],
      emoji_max_per_msg INT,
      emoji_max_per_min INT,
      max_msgs_per_10s INT,
      per_user_daily_limit INT,   -- NEU
      quota_notify TEXT,          -- NEU: 'off'|'smart'|'always'
      action_primary TEXT,        -- 'delete'|'warn'|'mute'
      action_secondary TEXT,      -- 'none'|'mute'|'ban'
      escalation_threshold INT,
      PRIMARY KEY(chat_id, topic_id)
    );
""")
    # Migration für bestehende Installationen
    cur.execute("ALTER TABLE spam_policy_topic ADD COLUMN IF NOT EXISTS per_user_daily_limit INT;")
    cur.execute("ALTER TABLE spam_policy_topic ADD COLUMN IF NOT EXISTS quota_notify TEXT;")

def _default_policy():
    return {
        "level": "off",
        "link_whitelist": [],
        "domain_blacklist": [],
        "only_admin_links": False,  # ← NEU
        "emoji_max_per_msg": 0,
        "emoji_max_per_min": 0,
        "max_msgs_per_10s": 0,
        "per_user_daily_limit": 0,   # ← NEU
        "quota_notify": "smart",     # ← NEU: 'off'|'smart'|'always'
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
def set_spam_policy_topic(cur, chat_id: int, topic_id: int, **fields):
    """
    Upsert von Topic-Overrides. Erlaubte Keys sind die aus _default_policy():
    level, link_whitelist, domain_blacklist, emoji_max_per_msg, emoji_max_per_min,
    max_msgs_per_10s, action_primary, action_secondary, escalation_threshold
    """
    if not fields:
        return
    allowed = set(_default_policy().keys())
    col_names = []
    values = []
    updates = []

    for k, v in fields.items():
        if k in allowed:
            col_names.append(k)
            values.append(v)
            updates.append(f"{k}=EXCLUDED.{k}")

    if not col_names:
        return

    placeholders = ", ".join(["%s"] * len(col_names))
    cols = ", ".join(col_names)
    sql = f"""
        INSERT INTO spam_policy_topic (chat_id, topic_id, {cols})
        VALUES (%s, %s, {placeholders})
        ON CONFLICT (chat_id, topic_id) DO UPDATE SET {", ".join(updates)};
    """
    cur.execute(sql, (chat_id, topic_id, *values))

@_with_cursor
def get_spam_policy_topic(cur, chat_id:int, topic_id:int) -> dict|None:
    cols = [
        "level", "link_whitelist", "domain_blacklist",
        "emoji_max_per_msg", "emoji_max_per_min", "max_msgs_per_10s",
        "per_user_daily_limit", "quota_notify",
        "action_primary", "action_secondary", "escalation_threshold"
    ]
    cur.execute(f"""
        SELECT {", ".join(cols)}
          FROM spam_policy_topic
         WHERE chat_id=%s AND topic_id=%s;
    """, (chat_id, topic_id))
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else None

@_with_cursor
def delete_spam_policy_topic(cur, chat_id:int, topic_id:int):
    cur.execute("DELETE FROM spam_policy_topic WHERE chat_id=%s AND topic_id=%s;", (chat_id, topic_id))

def _extract_link_flags(link_settings):
    """
    Normalisiert link_settings auf:
      (link_protection_enabled, link_warning_enabled, link_warning_text, link_exceptions_enabled)
    Erlaubt dict, tuple/list (beliebige Länge).
    """
    DEFAULT_TEXT = '⚠️ Nur Admins dürfen Links posten.'

    ls = link_settings
    # dict-Variante (beliebige Key-Namen zulassen – inkl. Aliase)
    if isinstance(ls, dict):
        prot_on = bool(
            ls.get("link_protection_enabled")
            or ls.get("admins_only")
            or ls.get("only_admin_links")
            or ls.get("protection")
        )
        warn_on = bool(ls.get("link_warning_enabled") or ls.get("warning_on"))
        warn_text = ls.get("link_warning_text") or ls.get("warning_text") or DEFAULT_TEXT
        except_on = bool(ls.get("link_exceptions_enabled") or ls.get("exceptions_on"))
        return prot_on, warn_on, warn_text, except_on

    # tuple/list-Variante (auf 4 Elemente trimmen/padden)
    if isinstance(ls, (tuple, list)):
        a = list(ls) + [False, False, DEFAULT_TEXT, True]
        return bool(a[0]), bool(a[1]), a[2], bool(a[3])

    # Fallback
    return False, False, DEFAULT_TEXT, True

def effective_spam_policy(chat_id:int, topic_id:int|None, link_settings) -> dict:
    """
    link_settings kann tuple/list oder dict sein.
    """
    prot_on, warn_on, warn_text, except_on = _extract_link_flags(link_settings)

    base = _default_policy()
    base["level"] = "strict" if prot_on else "off"

    # Preset anhand Level
    base.update(_LEVEL_PRESETS.get(base["level"], {}))

    # Topic-Overrides mergen
    if topic_id:
        ov = get_spam_policy_topic(chat_id, topic_id)
        if ov:
            for k, v in ov.items():
                if v is not None:
                    base[k] = v
            # ggf. erneut Preset ziehen, falls das Override den Level geändert hat
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
def set_mood_topic(cur, chat_id: int, topic_id: Optional[int]):
    logger.info(f"DB: Speichere Mood-Topic für Chat {chat_id}: {topic_id}")
    cur.execute(
        "INSERT INTO mood_topics (chat_id, topic_id) VALUES (%s, %s) "
        "ON CONFLICT (chat_id) DO UPDATE SET topic_id = EXCLUDED.topic_id;",
        (chat_id, topic_id)
    )
    # Auch in group_settings für Kompatibilität
    cur.execute(
        "INSERT INTO group_settings (chat_id, mood_topic_id) VALUES (%s, %s) "
        "ON CONFLICT (chat_id) DO UPDATE SET mood_topic_id = EXCLUDED.mood_topic_id;",
        (chat_id, topic_id or 0)
    )

@_with_cursor  
def get_mood_topic(cur, chat_id: int) -> Optional[int]:
    # Neue Tabelle zuerst
    cur.execute("SELECT topic_id FROM mood_topics WHERE chat_id = %s;", (chat_id,))
    row = cur.fetchone()
    if row and row[0]:
        return int(row[0])
    # Fallback Legacy
    cur.execute("SELECT mood_topic_id FROM group_settings WHERE chat_id = %s;", (chat_id,))
    row = cur.fetchone()
    return int(row[0]) if row and row[0] else None

# --- Welcome / Rules / Farewell ---
@_with_cursor
def set_welcome(cur, chat_id: int, photo_id: Optional[str], text: Optional[str]):
    try:
        logger.info(f"DB: Speichere Welcome für Chat {chat_id}. Photo: {bool(photo_id)}, Text: '{text[:50] if text else 'None'}...'")
        cur.execute(
            "INSERT INTO welcome (chat_id, photo_id, text) VALUES (%s, %s, %s) "
            "ON CONFLICT (chat_id) DO UPDATE SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;",
            (chat_id, photo_id, text)
        )
        logger.info(f"DB: Welcome für Chat {chat_id} erfolgreich gespeichert.")
    except Exception as e:
        logger.error(f"DB-Fehler in set_welcome: {e}", exc_info=True)
        raise

@_with_cursor
def get_welcome(cur, chat_id: int) -> Optional[Tuple[str, str]]:
    cur.execute("SELECT photo_id, text FROM welcome WHERE chat_id = %s;", (chat_id,))
    return cur.fetchone()

@_with_cursor
def delete_welcome(cur, chat_id: int):
    cur.execute("DELETE FROM welcome WHERE chat_id = %s;", (chat_id,))

@_with_cursor
def set_rules(cur, chat_id: int, photo_id: Optional[str], text: Optional[str]):
    try:
        logger.info(f"DB: Speichere Rules für Chat {chat_id}. Photo: {bool(photo_id)}, Text: '{text[:50] if text else 'None'}...'")
        cur.execute(
            "INSERT INTO rules (chat_id, photo_id, text) VALUES (%s, %s, %s) "
            "ON CONFLICT (chat_id) DO UPDATE SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;",
            (chat_id, photo_id, text)
        )
        logger.info(f"DB: Rules für Chat {chat_id} erfolgreich gespeichert.")
    except Exception as e:
        logger.error(f"DB-Fehler in set_rules: {e}", exc_info=True)
        raise

@_with_cursor
def get_rules(cur, chat_id: int) -> Optional[Tuple[str, str]]:
    cur.execute("SELECT photo_id, text FROM rules WHERE chat_id = %s;", (chat_id,))
    return cur.fetchone()

@_with_cursor
def delete_rules(cur, chat_id: int):
    cur.execute("DELETE FROM rules WHERE chat_id = %s;", (chat_id,))

@_with_cursor
def set_farewell(cur, chat_id: int, photo_id: Optional[str], text: Optional[str]):
    try:
        logger.info(f"DB: Speichere Farewell für Chat {chat_id}. Photo: {bool(photo_id)}, Text: '{text[:50] if text else 'None'}...'")
        cur.execute(
            "INSERT INTO farewell (chat_id, photo_id, text) VALUES (%s, %s, %s) "
            "ON CONFLICT (chat_id) DO UPDATE SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;",
            (chat_id, photo_id, text)
        )
        logger.info(f"DB: Farewell für Chat {chat_id} erfolgreich gespeichert.")
    except Exception as e:
        logger.error(f"DB-Fehler in set_farewell: {e}", exc_info=True)
        raise

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
def get_rss_feed_options(cur, chat_id:int, url:str):
    """
    Liefert aktuelle Optionen für einen Feed.
    Rückgabe: {"post_images": bool, "enabled": bool} oder None falls nicht gefunden.
    """
    cur.execute("""
        SELECT post_images, enabled
          FROM rss_feeds
         WHERE chat_id=%s AND url=%s;
    """, (chat_id, url))
    row = cur.fetchone()
    if not row:
        return None
    return {"post_images": bool(row[0]), "enabled": bool(row[1])}

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

# --- KI ---

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
def set_pro_until(cur, chat_id: int, until: datetime | None, tier: str = "pro"):
    if until is not None and until <= datetime.now(ZoneInfo("UTC")):
        cur.execute("""
            INSERT INTO group_subscriptions (chat_id, tier, valid_until, updated_at)
            VALUES (%s, 'free', NULL, NOW())
            ON CONFLICT (chat_id) DO UPDATE SET tier='free', valid_until=NULL, updated_at=NOW();
        """, (chat_id,))
        return
    cur.execute("""
        INSERT INTO group_subscriptions (chat_id, tier, valid_until, updated_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (chat_id) DO UPDATE SET tier=EXCLUDED.tier, valid_until=EXCLUDED.valid_until, updated_at=NOW();
    """, (chat_id, tier, until))

@_with_cursor
def is_pro_chat(cur, chat_id: int) -> bool:
    cur.execute("SELECT tier, valid_until FROM group_subscriptions WHERE chat_id=%s;", (chat_id,))
    r = cur.fetchone()
    if not r:
        return False
    tier, until = r
    if tier not in ("pro", "pro_plus"):
        return False
    if until is None:
        return True
    return until > datetime.now(ZoneInfo("UTC"))

@_with_cursor
def get_subscription_info(cur, chat_id: int) -> dict:
    cur.execute("SELECT tier, valid_until FROM group_subscriptions WHERE chat_id=%s;", (chat_id,))
    r = cur.fetchone()
    if not r:
        return {"tier": "free", "valid_until": None, "active": False}
    tier, until = r
    active = tier in ("pro","pro_plus") and (until is None or until > datetime.utcnow())
    return {"tier": tier, "valid_until": until, "active": active}

@_with_cursor
def list_candidate_chats_for_ads(cur) -> List[int]:
    cur.execute("SELECT DISTINCT chat_id FROM message_logs WHERE timestamp > NOW() - INTERVAL '30 days';")
    return [r[0] for r in (cur.fetchall() or [])]

@_with_cursor
def set_adv_topic(cur, chat_id:int, topic_id:int|None):
    cur.execute("""
      INSERT INTO adv_settings (chat_id, adv_topic_id)
      VALUES (%s, %s)
      ON CONFLICT (chat_id) DO UPDATE SET adv_topic_id=EXCLUDED.adv_topic_id;
    """, (chat_id, topic_id))

@_with_cursor
def get_adv_settings(cur, chat_id:int) -> dict:
    cur.execute("""
      SELECT adv_enabled, adv_topic_id, min_gap_min, daily_cap, every_n_messages,
             label, quiet_start_min, quiet_end_min, last_adv_ts
        FROM adv_settings WHERE chat_id=%s;
    """, (chat_id,))
    r = cur.fetchone()
    if not r:
        # Defaults, falls noch nie gesetzt
        return {
          "adv_enabled": True, "adv_topic_id": None, "min_gap_min": 240,
          "daily_cap": 2, "every_n_messages": 0, "label": "Anzeige",
          "quiet_start_min": 1320, "quiet_end_min": 360, "last_adv_ts": None
        }
    (en, tid, gap, cap, nmsg, label, qs, qe, last_ts) = r
    return {"adv_enabled":en, "adv_topic_id":tid, "min_gap_min":gap, "daily_cap":cap,
            "every_n_messages":nmsg, "label":label, "quiet_start_min":qs,
            "quiet_end_min":qe, "last_adv_ts":last_ts}

@_with_cursor
def set_adv_settings(cur, chat_id:int, **fields):
    allowed = {"adv_enabled","min_gap_min","daily_cap","every_n_messages","label","quiet_start_min","quiet_end_min"}
    cols, vals = [], []
    for k,v in fields.items():
        if k in allowed:
            cols.append(f"{k}=%s"); vals.append(v)
    if not cols: return
    cur.execute(f"""
      INSERT INTO adv_settings (chat_id) VALUES (%s)
      ON CONFLICT (chat_id) DO UPDATE SET {", ".join(cols)};
    """, (chat_id, *vals))

@_with_cursor
def add_campaign(cur, title:str, body_text:str, link_url:str,
                 media_url:str|None=None, cta_label:str='Mehr erfahren',
                 weight:int=1, start_ts=None, end_ts=None, created_by:int|None=None) -> int:
    cur.execute("""
      INSERT INTO adv_campaigns (title, body_text, media_url, link_url, cta_label, weight, start_ts, end_ts, created_by)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
      RETURNING campaign_id;
    """, (title, body_text, media_url, link_url, cta_label, weight, start_ts, end_ts, created_by))
    return cur.fetchone()[0]

@_with_cursor
def list_active_campaigns(cur):
    cur.execute("""
      SELECT campaign_id, title, body_text, media_url, link_url, cta_label, weight
        FROM adv_campaigns
       WHERE enabled = TRUE
         AND (start_ts IS NULL OR NOW() >= start_ts)
         AND (end_ts   IS NULL OR NOW() <= end_ts)
    """)
    return cur.fetchall()

@_with_cursor
def record_impression(cur, chat_id:int, campaign_id:int, message_id:int):
    cur.execute("INSERT INTO adv_impressions (chat_id, campaign_id, message_id) VALUES (%s,%s,%s);", (chat_id, campaign_id, message_id))

@_with_cursor
def update_last_adv_ts(cur, chat_id:int):
    cur.execute("UPDATE adv_settings SET last_adv_ts=NOW() WHERE chat_id=%s;", (chat_id,))

@_with_cursor
def count_ads_today(cur, chat_id:int) -> int:
    cur.execute("SELECT COUNT(*) FROM adv_impressions WHERE chat_id=%s AND ts::date = NOW()::date;", (chat_id,))
    return int(cur.fetchone()[0])

@_with_cursor
def messages_since(cur, chat_id:int, since_ts) -> int:
    cur.execute("SELECT COUNT(*) FROM message_logs WHERE chat_id=%s AND timestamp > %s;", (chat_id, since_ts))
    return int(cur.fetchone()[0])

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

def _norm_dom(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r'^(https?://)?(www\.)?', '', s)
    return s.strip('/')

@_with_cursor
def get_effective_link_policy(cur, chat_id: int, topic_id: int | None):
    # 1) Gruppenweite Link-Flags
    cur.execute("""
        SELECT link_protection_enabled, link_warning_enabled, link_warning_text
          FROM group_settings WHERE chat_id=%s;
    """, (chat_id,))
    row = cur.fetchone() or (False, False, '⚠️ Nur Admins dürfen Links posten.')
    admins_only  = bool(row[0])
    warn_enabled = bool(row[1])
    warn_text    = row[2] or '⚠️ Nur Admins dürfen Links posten.'

    # 2) Basis aus spam_policy (ohne topic_id) – nur Whitelist/Blacklist/Aktion
    cur.execute("""
        SELECT link_whitelist, domain_blacklist, action_primary
          FROM spam_policy
         WHERE chat_id=%s;
    """, (chat_id,))
    wl_base, bl_base, act = cur.fetchone() or ([], [], 'delete')
    wl = { _norm_dom(d) for d in (wl_base or []) }
    bl = { _norm_dom(d) for d in (bl_base or []) }
    action = act or 'delete'

    # 3) Topic-Overrides aus spam_policy_topic mergen
    if topic_id is not None:
        cur.execute("""
            SELECT link_whitelist, domain_blacklist, action_primary
              FROM spam_policy_topic
             WHERE chat_id=%s AND topic_id=%s;
        """, (chat_id, int(topic_id)))
        row = cur.fetchone()
        if row:
            wl_top, bl_top, act_top = row
            if wl_top: wl |= { _norm_dom(d) for d in wl_top }
            if bl_top: bl |= { _norm_dom(d) for d in bl_top }
            if act_top: action = act_top

    return {
        "admins_only": admins_only,
        "warning_enabled": warn_enabled,
        "warning_text": warn_text,
        "whitelist": sorted(wl),
        "blacklist": sorted(bl),
        "action": action or "delete",
    }

def _pending_inputs_col(cur) -> str:
    """Ermittelt, ob pending_inputs die Spalte 'chat_id' oder 'ctx_chat_id' hat."""
    global _pi_col_cache
    if _pi_col_cache is not None:
        return _pi_col_cache
    cur.execute("""
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name   = 'pending_inputs'
           AND column_name IN ('chat_id','ctx_chat_id')
         ORDER BY CASE column_name WHEN 'chat_id' THEN 1 ELSE 2 END
         LIMIT 1;
    """)
    row = cur.fetchone()
    _pi_col_cache = row[0] if row else 'chat_id'   # Default falls Tabelle leer/neu
    return _pi_col_cache

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

def _default_policy():
    return {
        "level": "off",
        "link_whitelist": [],
        "domain_blacklist": [],
        "only_admin_links": False,  # ← NEU
        "emoji_max_per_msg": 0,
        "emoji_max_per_min": 0,
        "max_msgs_per_10s": 0,
        "per_user_daily_limit": 0,   # ← NEU
        "quota_notify": "smart",     # ← NEU: 'off'|'smart'|'always'
        "action_primary": "delete",
        "action_secondary": "none",
        "escalation_threshold": 3
    }


@_with_cursor
def count_topic_user_messages_between(cur, chat_id:int, topic_id:int, user_id:int, start_dt, end_dt) -> int:
    cur.execute("""
        SELECT COUNT(*) FROM message_logs
         WHERE chat_id=%s AND topic_id=%s AND user_id=%s
           AND timestamp >= %s AND timestamp < %s
    """, (chat_id, topic_id, user_id, start_dt, end_dt))
    row = cur.fetchone()
    return int(row[0]) if row else 0

def count_topic_user_messages_today(chat_id:int, topic_id:int, user_id:int, tz:str="Europe/Berlin") -> int:
    tzinfo = ZoneInfo(tz)
    start_local = datetime.now(tzinfo).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local   = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(ZoneInfo("UTC"))
    end_utc   = end_local.astimezone(ZoneInfo("UTC"))
    return count_topic_user_messages_between(chat_id, topic_id, user_id, start_utc, end_utc)

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

@_with_cursor
def init_ads_schema(cur):
    # Pro-Abo Tabelle hinzufügen
    cur.execute("""
        CREATE TABLE IF NOT EXISTS group_subscriptions (
          chat_id     BIGINT PRIMARY KEY,
          tier        TEXT NOT NULL DEFAULT 'free',
          valid_until TIMESTAMPTZ,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_groupsub_valid ON group_subscriptions(valid_until DESC);")
    
    # Mood Topics Tabelle hinzufügen
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mood_topics (
            chat_id BIGINT PRIMARY KEY,
            topic_id BIGINT
        );
    """)
 
@_with_cursor
def ensure_payments_schema(cur):
    cur.execute("""
      CREATE TABLE IF NOT EXISTS payment_orders(
        order_id TEXT PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        provider TEXT NOT NULL,
        plan_key TEXT NOT NULL,
        price_eur NUMERIC NOT NULL,
        months INT NOT NULL,
        user_id BIGINT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        paid_at TIMESTAMPTZ
      );
    """)
    cur.execute("""
      CREATE TABLE IF NOT EXISTS payment_events(
        id BIGSERIAL PRIMARY KEY,
        order_id TEXT NOT NULL REFERENCES payment_orders(order_id),
        provider TEXT NOT NULL,
        payload JSONB,
        ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
      );
    """)

@_with_cursor
def create_payment_order(cur, order_id:str, chat_id:int, provider:str, plan_key:str, price_eur:str, months:int, user_id:int):
    cur.execute("""
      INSERT INTO payment_orders(order_id, chat_id, provider, plan_key, price_eur, months, user_id)
      VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING;
    """, (order_id, chat_id, provider, plan_key, price_eur, months, user_id))

@_with_cursor
def mark_payment_paid(cur, order_id:str, provider:str) -> tuple[bool,int,int]:
    cur.execute("UPDATE payment_orders SET status='paid', paid_at=NOW() WHERE order_id=%s AND status<>'paid' RETURNING chat_id, months;", (order_id,))
    row = cur.fetchone()
    return (True, row[0], row[1]) if row else (False, 0, 0)


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
        
        try:
            # Tabelle anlegen, falls sie nicht existiert (gleich mit 'chat_id')
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pending_inputs (
                chat_id   BIGINT NOT NULL,
                user_id   BIGINT NOT NULL,
                key       TEXT   NOT NULL,
                payload   JSONB  NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (chat_id, user_id, key)
                );
            """)
            # Altspalte ctx_chat_id -> chat_id migrieren (idempotent)
            cur.execute("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema=current_schema()
                AND table_name='pending_inputs'
                AND column_name='ctx_chat_id';
            """)
            if cur.fetchone():
                cur.execute("ALTER TABLE pending_inputs ADD COLUMN IF NOT EXISTS chat_id BIGINT;")
                cur.execute("UPDATE pending_inputs SET chat_id = ctx_chat_id WHERE chat_id IS NULL;")
                cur.execute("ALTER TABLE pending_inputs DROP CONSTRAINT IF EXISTS pending_inputs_pkey;")
                cur.execute("ALTER TABLE pending_inputs ADD CONSTRAINT pending_inputs_pkey PRIMARY KEY (chat_id, user_id, key);")
                cur.execute("ALTER TABLE pending_inputs DROP COLUMN IF EXISTS ctx_chat_id;")
        except Exception as e:
            logger.warning(f"[pending_inputs] Migration übersprungen: {e}")
        
        cur.execute("ALTER TABLE message_logs  ADD COLUMN IF NOT EXISTS chat_id  BIGINT;")
        cur.execute("ALTER TABLE message_logs  ADD COLUMN IF NOT EXISTS user_id  BIGINT;")
        cur.execute("ALTER TABLE message_logs  ADD COLUMN IF NOT EXISTS timestamp TIMESTAMPTZ DEFAULT NOW();")
        cur.execute("ALTER TABLE member_events ADD COLUMN IF NOT EXISTS chat_id  BIGINT;")
        cur.execute("ALTER TABLE member_events ADD COLUMN IF NOT EXISTS user_id  BIGINT;")
        cur.execute("ALTER TABLE member_events ADD COLUMN IF NOT EXISTS ts      TIMESTAMPTZ DEFAULT NOW();")
        cur.execute("ALTER TABLE member_events ADD COLUMN IF NOT EXISTS event_type TEXT;")
        cur.execute("ALTER TABLE rss_feeds ADD COLUMN IF NOT EXISTS last_etag     TEXT;")
        cur.execute("ALTER TABLE rss_feeds ADD COLUMN IF NOT EXISTS last_modified TEXT;")
        cur.execute("ALTER TABLE rss_feeds ADD COLUMN IF NOT EXISTS post_images   BOOLEAN;")
        cur.execute("ALTER TABLE rss_feeds ADD COLUMN IF NOT EXISTS enabled       BOOLEAN;")
        cur.execute("ALTER TABLE rss_feeds ALTER COLUMN post_images SET DEFAULT FALSE;")
        cur.execute("ALTER TABLE rss_feeds ALTER COLUMN enabled     SET DEFAULT TRUE;")
        cur.execute("UPDATE rss_feeds SET post_images=FALSE WHERE post_images IS NULL;")
        cur.execute("UPDATE rss_feeds SET enabled=TRUE  WHERE enabled     IS NULL;")
        # --- reply_times auf neues Schema heben (idempotent) ---
        cur.execute("CREATE TABLE IF NOT EXISTS reply_times (chat_id BIGINT, question_msg_id BIGINT, question_user BIGINT, answer_msg_id BIGINT, answer_user BIGINT, delta_ms BIGINT, ts TIMESTAMP DEFAULT NOW());")
        cur.execute("ALTER TABLE reply_times ADD COLUMN IF NOT EXISTS chat_id BIGINT;")
        cur.execute("ALTER TABLE reply_times ADD COLUMN IF NOT EXISTS question_msg_id BIGINT;")
        cur.execute("ALTER TABLE reply_times ADD COLUMN IF NOT EXISTS question_user BIGINT;")
        cur.execute("ALTER TABLE reply_times ADD COLUMN IF NOT EXISTS answer_msg_id BIGINT;")
        cur.execute("ALTER TABLE reply_times ADD COLUMN IF NOT EXISTS answer_user BIGINT;")
        cur.execute("ALTER TABLE reply_times ADD COLUMN IF NOT EXISTS delta_ms BIGINT;")
        cur.execute("ALTER TABLE reply_times ADD COLUMN IF NOT EXISTS ts TIMESTAMP DEFAULT NOW();")
        # sinnvolle Indizes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_reply_times_chat_ts ON reply_times(chat_id, ts DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_reply_times_ans_user ON reply_times(chat_id, answer_user, ts DESC);")
        # message_logs: Topic-Spalte + sinnvoller Index
        cur.execute("ALTER TABLE message_logs ADD COLUMN IF NOT EXISTS topic_id BIGINT;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_msglogs_topic_user_ts ON message_logs(chat_id, topic_id, user_id, timestamp DESC);")
        # neue Spalte in spam_policy_topic
        cur.execute("ALTER TABLE spam_policy_topic ADD COLUMN IF NOT EXISTS per_user_daily_limit INT DEFAULT 0;")
        cur.execute("ALTER TABLE spam_policy_topic ADD COLUMN IF NOT EXISTS only_admin_links BOOLEAN NOT NULL DEFAULT FALSE;")
        # message_logs: Topic-Spalte & Index (falls nicht vorhanden)
        cur.execute("ALTER TABLE message_logs ADD COLUMN IF NOT EXISTS topic_id BIGINT;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_msglogs_topic_user_ts ON message_logs(chat_id, topic_id, user_id, timestamp DESC);")

        # Spam-Topic-Policy: Tageslimit + Notify-Modus
        cur.execute("ALTER TABLE spam_policy_topic ADD COLUMN IF NOT EXISTS per_user_daily_limit INT DEFAULT 0;")
        cur.execute("ALTER TABLE spam_policy_topic ADD COLUMN IF NOT EXISTS quota_notify TEXT DEFAULT 'smart';")

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

        cur.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS clean_deleted_notify BOOLEAN NOT NULL DEFAULT FALSE;")

        cur.execute("""
        ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS clean_deleted_enabled  BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS clean_deleted_hh       INT;
        ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS clean_deleted_mm       INT;
        ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS clean_deleted_weekday  INT;  -- 0=Mo … 6=So, NULL=täglich
        ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS clean_deleted_demote   BOOLEAN NOT NULL DEFAULT FALSE; -- <— KORREKT
        ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS clean_deleted_notify   BOOLEAN NOT NULL DEFAULT TRUE;  -- für „Benachrichtigung“
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
def init_ads_schema():
    """Initialize advertising system schema"""
    init_db()  # Ensure base schema exists first

def init_all_schemas():
    """Initialize all database schemas including ads"""
    logger.info("Initializing all database schemas...")
    init_db()
    ensure_multi_bot_schema()
    init_ads_schema()  # Hinzufügen
    migrate_db()
    migrate_stats_rollup()
    ensure_spam_topic_schema()
    ensure_forum_topics_schema()
    ensure_ai_moderation_schema()
    logger.info("✅ All schemas initialized successfully")

if __name__ == "__main__":
    init_all_schemas()
