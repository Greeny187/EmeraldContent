"""database.py — Emerald Support Bot Database (single source of truth)

✅ Only file for DB operations (no sql.py needed)
✅ Psycopg2 (sync) but safe to call from FastAPI using anyio.to_thread
✅ Support-only schema: users, tickets, messages, kb, tenants
✅ Strong logging at critical points
"""

import logging
import os
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("bot.support.db")


def get_db_connection():
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.error("DATABASE_URL missing")
        return None
    try:
        conn = psycopg2.connect(dsn)
        return conn
    except Exception:
        logger.exception("DB connection failed")
        return None


# ---------------- Schema ----------------

def init_all_schemas() -> None:
    """Initialize Support database schemas (idempotent)."""
    conn = get_db_connection()
    if not conn:
        logger.error("init_all_schemas: no DB connection")
        return

    cur = None
    try:
        cur = conn.cursor()

        # Tenants
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id SERIAL PRIMARY KEY,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)

        # Tenant ↔ Telegram chats/groups mapping
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenant_groups (
                tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                chat_id BIGINT UNIQUE NOT NULL,
                title TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)

        # Users
        cur.execute("""
            CREATE TABLE IF NOT EXISTS support_users (
                user_id BIGINT PRIMARY KEY,
                handle TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            );
        """)

        # Tickets
        cur.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES support_users(user_id) ON DELETE CASCADE,
                tenant_id INTEGER REFERENCES tenants(id) ON DELETE SET NULL,
                category VARCHAR(48) DEFAULT 'allgemein',
                subject VARCHAR(140) NOT NULL,
                status VARCHAR(32) DEFAULT 'neu',
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now(),
                closed_at TIMESTAMPTZ
            );
        """)

        # Messages
        cur.execute("""
            CREATE TABLE IF NOT EXISTS support_messages (
                id SERIAL PRIMARY KEY,
                ticket_id INTEGER NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
                author_user_id BIGINT NOT NULL,
                is_public BOOLEAN DEFAULT TRUE,
                text TEXT NOT NULL,
                attachments JSONB,
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)

        # KB articles
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kb_articles (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                tags TEXT[],
                score INTEGER DEFAULT 0,
                updated_at TIMESTAMPTZ DEFAULT now()
            );
        """)

        # Helpful indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_support_tickets_user ON support_tickets(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_support_tickets_tenant ON support_tickets(tenant_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_support_messages_ticket ON support_messages(ticket_id);")

        conn.commit()
        logger.info("✅ Support schemas initialized/verified")
    except Exception:
        logger.exception("Schema init failed")
        conn.rollback()
    finally:
        if cur:
            cur.close()
        conn.close()


# ---------------- Core ops ----------------

def upsert_user(user: Dict[str, Any]) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO support_users (user_id, handle, first_name, last_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET
                handle = EXCLUDED.handle,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                updated_at = now()
            """,
            (user["user_id"], user.get("username"), user.get("first_name"), user.get("last_name")),
        )
        conn.commit()
        logger.info("User upserted: user_id=%s", user["user_id"])
        return True
    except Exception:
        logger.exception("upsert_user failed: user_id=%s", user.get("user_id"))
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        conn.close()


def resolve_tenant_id_by_chat(chat_id: int) -> Optional[int]:
    conn = get_db_connection()
    if not conn:
        return None
    cur = None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT tenant_id FROM tenant_groups WHERE chat_id=%s LIMIT 1", (chat_id,))
        row = cur.fetchone()
        return int(row["tenant_id"]) if row else None
    except Exception:
        logger.exception("resolve_tenant_id_by_chat failed: chat_id=%s", chat_id)
        return None
    finally:
        if cur:
            cur.close()
        conn.close()


def ensure_tenant_for_chat(chat_id: int, title: Optional[str] = None, slug: Optional[str] = None) -> int:
    existing = resolve_tenant_id_by_chat(chat_id)
    if existing:
        return existing

    conn = get_db_connection()
    if not conn:
        raise RuntimeError("No DB connection")

    cur = None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        gen_slug = (slug or f"tg-{chat_id}").lower().replace(" ", "-")

        # Create tenant if missing
        cur.execute(
            """
            INSERT INTO tenants (slug, name)
            VALUES (%s, %s)
            ON CONFLICT (slug) DO NOTHING
            RETURNING id
            """,
            (gen_slug, title or gen_slug),
        )
        row = cur.fetchone()
        if row and row.get("id"):
            tid = int(row["id"])
        else:
            cur.execute("SELECT id FROM tenants WHERE slug=%s LIMIT 1", (gen_slug,))
            row2 = cur.fetchone()
            if not row2:
                raise RuntimeError("Failed to create or find tenant")
            tid = int(row2["id"])

        # Create mapping
        cur.execute(
            """
            INSERT INTO tenant_groups (tenant_id, chat_id, title)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id) DO NOTHING
            """,
            (tid, chat_id, title),
        )

        conn.commit()
        logger.info("Tenant ensured: tenant_id=%s chat_id=%s", tid, chat_id)
        return tid
    except Exception:
        logger.exception("ensure_tenant_for_chat failed: chat_id=%s", chat_id)
        conn.rollback()
        raise
    finally:
        if cur:
            cur.close()
        conn.close()


def create_ticket(user_id: int, category: str, subject: str, body: str, tenant_id: Optional[int] = None) -> Optional[int]:
    conn = get_db_connection()
    if not conn:
        return None
    cur = None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            """
            INSERT INTO support_tickets (user_id, tenant_id, category, subject)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, tenant_id, category, subject),
        )
        row = cur.fetchone()
        tid = int(row["id"])

        cur.execute(
            """
            INSERT INTO support_messages (ticket_id, author_user_id, is_public, text)
            VALUES (%s, %s, TRUE, %s)
            """,
            (tid, user_id, body),
        )

        conn.commit()
        logger.info("Ticket created: id=%s user_id=%s tenant_id=%s", tid, user_id, tenant_id)
        return tid
    except Exception:
        logger.exception("create_ticket failed: user_id=%s", user_id)
        conn.rollback()
        return None
    finally:
        if cur:
            cur.close()
        conn.close()


def get_my_tickets(user_id: int, limit: int = 30, tenant_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    if not conn:
        return []
    cur = None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if tenant_id is not None:
            cur.execute(
                """
                SELECT id, category, subject, status, created_at, closed_at
                FROM support_tickets
                WHERE user_id=%s AND (tenant_id=%s OR tenant_id IS NULL)
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, tenant_id, limit),
            )
        else:
            cur.execute(
                """
                SELECT id, category, subject, status, created_at, closed_at
                FROM support_tickets
                WHERE user_id=%s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("get_my_tickets failed: user_id=%s", user_id)
        return []
    finally:
        if cur:
            cur.close()
        conn.close()


def get_ticket(user_id: int, ticket_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    if not conn:
        return None
    cur = None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            """
            SELECT id, user_id, category, subject, status, created_at, closed_at
            FROM support_tickets
            WHERE id=%s AND user_id=%s
            """,
            (ticket_id, user_id),
        )
        ticket = cur.fetchone()
        if not ticket:
            return None

        cur.execute(
            """
            SELECT id, author_user_id, is_public, text, attachments, created_at
            FROM support_messages
            WHERE ticket_id=%s AND is_public=TRUE
            ORDER BY created_at ASC
            """,
            (ticket_id,),
        )
        msgs = cur.fetchall()
        out = dict(ticket)
        out["messages"] = [dict(m) for m in msgs]
        return out
    except Exception:
        logger.exception("get_ticket failed: ticket_id=%s user_id=%s", ticket_id, user_id)
        return None
    finally:
        if cur:
            cur.close()
        conn.close()


def add_public_message(user_id: int, ticket_id: int, text: str) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    cur = None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("SELECT user_id FROM support_tickets WHERE id=%s", (ticket_id,))
        row = cur.fetchone()
        if not row or int(row["user_id"]) != int(user_id):
            logger.warning("add_public_message denied: ticket_id=%s user_id=%s", ticket_id, user_id)
            return False

        cur.execute(
            """
            INSERT INTO support_messages (ticket_id, author_user_id, is_public, text)
            VALUES (%s, %s, TRUE, %s)
            """,
            (ticket_id, user_id, text),
        )
        cur.execute("UPDATE support_tickets SET updated_at=now() WHERE id=%s", (ticket_id,))
        conn.commit()
        logger.info("Message added: ticket_id=%s user_id=%s", ticket_id, user_id)
        return True
    except Exception:
        logger.exception("add_public_message failed: ticket_id=%s user_id=%s", ticket_id, user_id)
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        conn.close()


def kb_search(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    if not query or len(query) < 2:
        return []
    conn = get_db_connection()
    if not conn:
        return []
    cur = None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        like = f"%{query}%"
        cur.execute(
            """
            SELECT id, title, left(body, 200) AS snippet, tags
            FROM kb_articles
            WHERE title ILIKE %s OR body ILIKE %s
            ORDER BY score DESC, updated_at DESC
            LIMIT %s
            """,
            (like, like, limit),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("kb_search failed: query=%s", query)
        return []
    finally:
        if cur:
            cur.close()
        conn.close()
