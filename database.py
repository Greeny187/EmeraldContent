import os
import psycopg2
from psycopg2 import sql
from urllib.parse import urlparse
from datetime import date
from typing import List, Dict, Tuple
import logging

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL ist nicht gesetzt. Bitte füge das Heroku Postgres Add-on und die Config Vars hinzu.")

result = urlparse(DATABASE_URL)
conn = psycopg2.connect(
    dbname=result.path.lstrip("/"),
    user=result.username,
    password=result.password,
    host=result.hostname,
    port=result.port,
    sslmode="require",
)
conn.autocommit = True

def init_db():
    with conn.cursor() as cur:
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
                PRIMARY KEY (chat_id, user_id)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS members (
                chat_id BIGINT,
                user_id BIGINT,
                PRIMARY KEY (chat_id, user_id)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                chat_id   BIGINT,
                stat_date DATE,
                user_id   BIGINT,
                messages  INT DEFAULT 0,
                PRIMARY KEY(chat_id, stat_date, user_id)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mood_meter (
                chat_id    BIGINT,
                message_id INT,
                user_id    BIGINT,
                mood       TEXT,
                PRIMARY KEY(chat_id, message_id, user_id)
            );
        """)

        # Bestehende Tabellen erweitern (Migrations)
        cur.execute("""
            ALTER TABLE groups
            ADD COLUMN IF NOT EXISTS welcome_topic_id BIGINT DEFAULT 0;
        """)
        cur.execute("""
            ALTER TABLE group_settings
            ADD COLUMN IF NOT EXISTS daily_stats_enabled BOOLEAN NOT NULL DEFAULT TRUE;
        """)
        cur.execute("""
            ALTER TABLE group_settings
            ADD COLUMN IF NOT EXISTS mood_question TEXT NOT NULL DEFAULT 'Wie fühlst du dich heute?';
        """)
        cur.execute("""
            ALTER TABLE group_settings
            ADD COLUMN IF NOT EXISTS rss_topic_id BIGINT NOT NULL DEFAULT 0;
        """)
        cur.execute("""
            ALTER TABLE members
            ADD COLUMN IF NOT EXISTS joined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
        """)


def migrate_db():
    logging.basicConfig(level=logging.INFO)
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        logging.info("Starte Migration: Gruppen, Einstellungen, Mitglieder…")

        # Bestehende Tabellen erweitern (Migrations)
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
                PRIMARY KEY (chat_id, user_id)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS members (
                chat_id BIGINT,
                user_id BIGINT,
                PRIMARY KEY (chat_id, user_id)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                chat_id   BIGINT,
                stat_date DATE,
                user_id   BIGINT,
                messages  INT DEFAULT 0,
                PRIMARY KEY(chat_id, stat_date, user_id)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mood_meter (
                chat_id    BIGINT,
                message_id INT,
                user_id    BIGINT,
                mood       TEXT,
                PRIMARY KEY(chat_id, message_id, user_id)
            );
        """)

        # Bestehende Tabellen erweitern (Migrations)
        cur.execute("""
            ALTER TABLE groups
            ADD COLUMN IF NOT EXISTS welcome_topic_id BIGINT DEFAULT 0;
        """)
        cur.execute("""
            ALTER TABLE group_settings
            ADD COLUMN IF NOT EXISTS daily_stats_enabled BOOLEAN NOT NULL DEFAULT TRUE;
        """)
        cur.execute("""
            ALTER TABLE group_settings
            ADD COLUMN IF NOT EXISTS mood_question TEXT NOT NULL DEFAULT 'Wie fühlst du dich heute?';
        """)
        cur.execute("""
            ALTER TABLE group_settings
            ADD COLUMN IF NOT EXISTS rss_topic_id BIGINT NOT NULL DEFAULT 0;
        """)
        cur.execute("""
            ALTER TABLE members
            ADD COLUMN IF NOT EXISTS joined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
        """)

        conn.commit()
        logging.info("Migration erfolgreich abgeschlossen.")
    except Exception as e:
        logging.error("Migration fehlgeschlagen: %s", e)
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    migrate_db()

# Gruppenverwaltung
def register_group(chat_id: int, title: str, welcome_topic_id: int = 0):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO groups (chat_id, title, welcome_topic_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title;
        """, (chat_id, title, welcome_topic_id))

def get_registered_groups():
    with conn.cursor() as cur:
        cur.execute("SELECT chat_id, title FROM groups;")
        return cur.fetchall()

def unregister_group(chat_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM groups WHERE chat_id = %s;", (chat_id,))

# Mitgliederverwaltung

def add_member(chat_id: int, user_id: int):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO members (chat_id, user_id, joined_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT DO NOTHING;
        """, (chat_id, user_id))

def remove_member(chat_id: int, user_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM members WHERE chat_id = %s AND user_id = %s;", (chat_id, user_id))

def list_members(chat_id: int) -> list[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM members WHERE chat_id = %s;", (chat_id,))
        return [row[0] for row in cur.fetchall()]

def inc_message_count(chat_id: int, user_id: int, stat_date: date):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO daily_stats (chat_id, stat_date, user_id, messages)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (chat_id, stat_date, user_id)
            DO UPDATE SET messages = daily_stats.messages + 1;
        """, (chat_id, stat_date, user_id))

def get_group_stats(chat_id: int, stat_date: date) -> List[Tuple[int, int]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT user_id, messages
            FROM daily_stats
            WHERE chat_id = %s AND stat_date = %s
            ORDER BY messages DESC
            LIMIT 3;
        """, (chat_id, stat_date))
        return cur.fetchall()

def get_new_members_count(chat_id: int, date: date) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM members
            WHERE chat_id = %s AND DATE(joined_at) = %s;
        """, (chat_id, date))
        return cur.fetchone()[0]

def save_mood(chat_id: int, message_id: int, user_id: int, mood: str):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO mood_meter(chat_id, message_id, user_id, mood)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (chat_id, message_id, user_id)
            DO UPDATE SET mood = EXCLUDED.mood;
        """, (chat_id, message_id, user_id, mood))

def get_mood_counts(chat_id: int, message_id: int) -> Dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT mood, COUNT(*) FROM mood_meter
            WHERE chat_id = %s AND message_id = %s
            GROUP BY mood;
        """, (chat_id, message_id))
        return dict(cur.fetchall())

def get_mood_question(chat_id: int) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT mood_question FROM group_settings WHERE chat_id = %s",
            (chat_id,)
        )
        row = cur.fetchone()
        return row[0] if row else "Wie fühlst du dich heute?"


def set_mood_question(chat_id: int, question: str):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO group_settings (chat_id, mood_question)
            VALUES (%s, %s)
            ON CONFLICT (chat_id) DO UPDATE
              SET mood_question = EXCLUDED.mood_question;
        """, (chat_id, question))

# Welcome
def set_welcome(chat_id: int, photo_id: str | None, text: str | None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO welcome (chat_id, photo_id, text)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE
            SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;
        """, (chat_id, photo_id, text))

def get_welcome(chat_id: int):
    with conn.cursor() as cur:
        cur.execute("SELECT photo_id, text FROM welcome WHERE chat_id = %s;", (chat_id,))
        return cur.fetchone()

def delete_welcome(chat_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM welcome WHERE chat_id = %s;", (chat_id,))

# Rules
def set_rules(chat_id: int, photo_id: str | None, text: str | None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO rules (chat_id, photo_id, text)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE
            SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;
        """, (chat_id, photo_id, text))

def get_rules(chat_id: int):
    with conn.cursor() as cur:
        cur.execute("SELECT photo_id, text FROM rules WHERE chat_id = %s;", (chat_id,))
        return cur.fetchone()

def delete_rules(chat_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM rules WHERE chat_id = %s;", (chat_id,))

# Farewell
def set_farewell(chat_id: int, photo_id: str | None, text: str | None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO farewell (chat_id, photo_id, text)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE
            SET photo_id = EXCLUDED.photo_id, text = EXCLUDED.text;
        """, (chat_id, photo_id, text))

def get_farewell(chat_id: int):
    with conn.cursor() as cur:
        cur.execute("SELECT photo_id, text FROM farewell WHERE chat_id = %s;", (chat_id,))
        return cur.fetchone()

def delete_farewell(chat_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM farewell WHERE chat_id = %s;", (chat_id,))

# RSS-Feeds

def set_rss_topic(chat_id: int, topic_id: int):
    with conn.cursor() as cur:
        # Falls group_settings-Zeile fehlt, daily_stats_enabled auf TRUE lassen
        cur.execute("""
            INSERT INTO group_settings (chat_id, daily_stats_enabled, rss_topic_id)
            VALUES (%s, TRUE, %s)
            ON CONFLICT (chat_id) DO UPDATE
                SET rss_topic_id = EXCLUDED.rss_topic_id;
        """, (chat_id, topic_id))

def get_rss_topic(chat_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rss_topic_id FROM group_settings WHERE chat_id = %s;",
            (chat_id,)
        )
        row = cur.fetchone()
        return row[0] if row else 0

def add_rss_feed(chat_id: int, url: str, topic_id: int):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO rss_feeds (chat_id, url, topic_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id, url) DO NOTHING;
        """, (chat_id, url, topic_id))

def list_rss_feeds(chat_id: int):
    with conn.cursor() as cur:
        cur.execute("SELECT url, topic_id FROM rss_feeds WHERE chat_id = %s;", (chat_id,))
        return cur.fetchall()

def remove_rss_feed(chat_id: int, url: str | None = None):
    with conn.cursor() as cur:
        if url:
            cur.execute("DELETE FROM rss_feeds WHERE chat_id = %s AND url = %s;", (chat_id, url))
        else:
            cur.execute("DELETE FROM rss_feeds WHERE chat_id = %s;", (chat_id,))

def get_rss_feeds():
    with conn.cursor() as cur:
        cur.execute("SELECT chat_id, url, topic_id FROM rss_feeds;")
        return cur.fetchall()

# Deduplizierung
def get_posted_links(chat_id: int):
    with conn.cursor() as cur:
        cur.execute("SELECT link FROM last_posts WHERE chat_id = %s;", (chat_id,))
        return {row[0] for row in cur.fetchall()}

def add_posted_link(chat_id: int, link: str):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO last_posts (chat_id, link)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING;
        """, (chat_id, link))

# Themenzuweisung für Linksperre-Ausnahme
def assign_topic(chat_id: int, user_id: int, topic_id: int = 0):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO user_topics (chat_id, user_id, topic_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id, user_id) DO UPDATE
            SET topic_id = EXCLUDED.topic_id;
        """, (chat_id, user_id, topic_id))

def remove_topic(chat_id: int, user_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_topics WHERE chat_id = %s AND user_id = %s;", (chat_id, user_id))

# Ausnahmen-Themenbesitzer abrufen
def get_topic_owners(chat_id: int) -> list[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM user_topics WHERE chat_id = %s;", (chat_id,))
        return [row[0] for row in cur.fetchall()]

def has_topic(chat_id: int, user_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM user_topics WHERE chat_id = %s AND user_id = %s;", (chat_id, user_id))
        return cur.fetchone() is not None

# Daily Stats
def is_daily_stats_enabled(chat_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT daily_stats_enabled FROM group_settings WHERE chat_id = %s",
            (chat_id,)
        )
        row = cur.fetchone()
        return row[0] if row else True  # Default = True

def set_daily_stats(chat_id: int, enabled: bool):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO group_settings(chat_id, daily_stats_enabled)
            VALUES (%s, %s)
            ON CONFLICT (chat_id) DO UPDATE
              SET daily_stats_enabled = EXCLUDED.daily_stats_enabled;
        """, (chat_id, enabled))


if __name__ == "__main__":
    init_db()
    migrate_db()
    print("✅ Migration abgeschlossen.")