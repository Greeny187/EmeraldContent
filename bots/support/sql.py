# sql.py — async psycopg3 Support Bot Database Layer (v1.0)
"""
Async database layer für Emerald Support Bot.
Nutzt psycopg (PostgreSQL async driver) mit Connection Pooling.
"""
import os
import logging
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

async def get_conn() -> psycopg.AsyncConnection:
    """Get async DB connection"""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable not set")
    return await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)

# ---------- Users ----------
async def upsert_user(user: Dict[str, Any]) -> bool:
    """Create or update user"""
    try:
        async with await get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO support_users (user_id, handle, first_name, last_name)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id)
                    DO UPDATE SET handle = EXCLUDED.handle,
                                  first_name = EXCLUDED.first_name,
                                  last_name = EXCLUDED.last_name
                    """,
                    (user["user_id"], user.get("username"), user.get("first_name"), user.get("last_name")),
                )
            await conn.commit()
        logger.debug(f"User {user['user_id']} upserted")
        return True
    except Exception as e:
        logger.error(f"Error upserting user {user.get('user_id')}: {e}")
        return False

# ---------- Tickets ----------
async def create_ticket(user_id: int, category: str, subject: str, body: str, tenant_id: Optional[int] = None) -> Optional[int]:
    """Create new ticket with initial message"""
    try:
        async with await get_conn() as conn:
            async with conn.cursor() as cur:
                # Validate inputs
                if not subject or not body:
                    raise ValueError("Subject and body are required")
                if len(subject) > 255 or len(body) > 4000:
                    raise ValueError("Subject or body too long")
                
                # Insert ticket
                await cur.execute(
                    """
                    INSERT INTO support_tickets (user_id, category, subject, tenant_id)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (user_id, category, subject, tenant_id),
                )
                row = await cur.fetchone()
                tid = row["id"]
                
                # Insert initial message
                await cur.execute(
                    """
                    INSERT INTO support_messages (ticket_id, author_user_id, is_public, text)
                    VALUES (%s, %s, TRUE, %s)
                    """,
                    (tid, user_id, body),
                )
            await conn.commit()
        logger.info(f"Ticket #{tid} created by user {user_id}")
        return tid
    except Exception as e:
        logger.error(f"Error creating ticket: {e}")
        return None

async def get_my_tickets(user_id: int, limit: int = 30, tenant_id: Optional[int] = None) -> List[Dict]:
    """Fetch all tickets for user"""
    try:
        async with await get_conn() as conn:
            async with conn.cursor() as cur:
                if tenant_id:
                    await cur.execute(
                        """
                        SELECT id, category, subject, status, created_at, closed_at
                        FROM support_tickets
                        WHERE user_id = %s AND (tenant_id = %s OR tenant_id IS NULL)
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (user_id, tenant_id, limit),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, category, subject, status, created_at, closed_at
                        FROM support_tickets
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (user_id, limit),
                    )
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching tickets for user {user_id}: {e}")
        return []

async def get_ticket(user_id: int, ticket_id: int) -> Optional[Dict]:
    """Fetch ticket with messages (only if user is owner)"""
    try:
        async with await get_conn() as conn:
            async with conn.cursor() as cur:
                # Fetch ticket
                await cur.execute(
                    """
                    SELECT id, user_id, category, subject, status, created_at, closed_at
                    FROM support_tickets
                    WHERE id = %s AND user_id = %s
                    """,
                    (ticket_id, user_id),
                )
                t = await cur.fetchone()
                if not t:
                    return None
                
                # Fetch public messages
                await cur.execute(
                    """
                    SELECT id, author_user_id, is_public, text, attachments, created_at
                    FROM support_messages
                    WHERE ticket_id = %s AND is_public = TRUE
                    ORDER BY created_at ASC
                    """,
                    (ticket_id,),
                )
                msgs = await cur.fetchall()
                return {**dict(t), "messages": [dict(m) for m in msgs]}
    except Exception as e:
        logger.error(f"Error fetching ticket {ticket_id}: {e}")
        return None

async def add_public_message(user_id: int, ticket_id: int, text: str) -> bool:
    """Add public message to ticket"""
    try:
        if not text or len(text) > 4000:
            raise ValueError("Invalid message text")
        
        async with await get_conn() as conn:
            async with conn.cursor() as cur:
                # Check ownership
                await cur.execute("SELECT user_id FROM support_tickets WHERE id = %s", (ticket_id,))
                row = await cur.fetchone()
                if not row or row["user_id"] != user_id:
                    logger.warning(f"Unauthorized message to ticket {ticket_id} by user {user_id}")
                    return False
                
                # Insert message
                await cur.execute(
                    """
                    INSERT INTO support_messages (ticket_id, author_user_id, is_public, text)
                    VALUES (%s, %s, TRUE, %s)
                    """,
                    (ticket_id, user_id, text),
                )
                
                # Update ticket timestamp
                await cur.execute(
                    "UPDATE support_tickets SET updated_at = now() WHERE id = %s",
                    (ticket_id,),
                )
            await conn.commit()
        logger.info(f"Message added to ticket #{ticket_id} by user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error adding message to ticket {ticket_id}: {e}")
        return False

async def close_ticket(ticket_id: int, user_id: int) -> bool:
    """Close ticket (only owner can close)"""
    try:
        async with await get_conn() as conn:
            async with conn.cursor() as cur:
                # Check ownership
                await cur.execute("SELECT user_id FROM support_tickets WHERE id = %s", (ticket_id,))
                row = await cur.fetchone()
                if not row or row["user_id"] != user_id:
                    return False
                
                # Update status
                await cur.execute(
                    """
                    UPDATE support_tickets
                    SET status = 'geloest', closed_at = now()
                    WHERE id = %s
                    """,
                    (ticket_id,),
                )
            await conn.commit()
        logger.info(f"Ticket #{ticket_id} closed by user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error closing ticket {ticket_id}: {e}")
        return False

# ---------- KB ----------
async def kb_search(query: str, limit: int = 8) -> List[Dict]:
    """Search knowledge base by title/body"""
    try:
        if not query or len(query) < 2:
            return []
        
        like = f"%{query}%"
        async with await get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, title, left(body, 200) AS snippet, tags
                    FROM kb_articles
                    WHERE title ILIKE %s OR body ILIKE %s
                    ORDER BY score DESC, updated_at DESC
                    LIMIT %s
                    """,
                    (like, like, limit),
                )
                rows = await cur.fetchall()
                result = [dict(r) for r in rows]
                logger.debug(f"KB search '{query}': {len(result)} results")
                return result
    except Exception as e:
        logger.error(f"Error searching KB: {e}")
        return []

# ---------- Group Settings ----------
async def save_group_settings(chat_id: int, title: Optional[str], data: dict, updated_by: Optional[int]) -> bool:
    """Save group settings as JSONB"""
    try:
        if not chat_id or not data:
            raise ValueError("Chat ID and data required")
        
        async with await get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO group_settings (chat_id, title, data, updated_by)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (chat_id)
                    DO UPDATE SET title = EXCLUDED.title,
                                  data = EXCLUDED.data,
                                  updated_by = EXCLUDED.updated_by,
                                  updated_at = now()
                    """,
                    (chat_id, title, data, updated_by),
                )
            await conn.commit()
        logger.info(f"Group settings saved for chat {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Error saving group settings for chat {chat_id}: {e}")
        return False

async def load_group_settings(chat_id: int) -> Dict:
    """Load group settings as dict"""
    try:
        async with await get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT data FROM group_settings WHERE chat_id = %s", (chat_id,))
                row = await cur.fetchone()
                if row and row.get("data"):
                    return dict(row["data"])
                return {}
    except Exception as e:
        logger.error(f"Error loading group settings for chat {chat_id}: {e}")
        return {}

async def load_group_stats(chat_id: int, days: int = 14) -> List[Dict]:
    """Load daily stats for group"""
    try:
        if days < 1 or days > 365:
            days = 14
        
        async with await get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT to_char(day_date, 'YYYY-MM-DD') AS date,
                           messages, active, joins, leaves, kicks, reply_p90_ms, spam_actions
                    FROM group_daily_agg
                    WHERE chat_id = %s AND day_date >= CURRENT_DATE - %s::int
                    ORDER BY day_date DESC
                    """,
                    (chat_id, days),
                )
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error loading stats for chat {chat_id}: {e}")
        return []

# ---------- Tenants ----------
async def resolve_tenant_id_by_chat(chat_id: int) -> Optional[int]:
    """Resolve tenant ID for chat"""
    try:
        async with await get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT tenant_id FROM tenant_groups WHERE chat_id = %s LIMIT 1",
                    (chat_id,),
                )
                row = await cur.fetchone()
                return row["tenant_id"] if row else None
    except Exception as e:
        logger.error(f"Error resolving tenant for chat {chat_id}: {e}")
        return None

async def ensure_tenant_for_chat(chat_id: int, title: Optional[str] = None, slug: Optional[str] = None) -> int:
    """Ensure tenant exists for chat (create if not exists)"""
    try:
        # 1. Check if mapping exists
        tid = await resolve_tenant_id_by_chat(chat_id)
        if tid:
            return tid
        
        # 2. Create new tenant
        gen_slug = (slug or f"tg-{chat_id}").lower().replace(" ", "-")
        
        async with await get_conn() as conn:
            async with conn.cursor() as cur:
                # Try to create tenant
                await cur.execute(
                    """
                    INSERT INTO tenants (slug, name)
                    VALUES (%s, %s)
                    ON CONFLICT (slug) DO NOTHING
                    RETURNING id
                    """,
                    (gen_slug, title or gen_slug),
                )
                row = await cur.fetchone()
                
                if row:
                    tid = row["id"]
                else:
                    # Fetch existing
                    await cur.execute("SELECT id FROM tenants WHERE slug = %s", (gen_slug,))
                    existing = await cur.fetchone()
                    tid = existing["id"] if existing else None
                
                if tid:
                    # Create mapping
                    await cur.execute(
                        """
                        INSERT INTO tenant_groups (tenant_id, chat_id, title)
                        VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (tid, chat_id, title),
                    )
            await conn.commit()
        
        logger.info(f"Tenant {tid} ensured for chat {chat_id}")
        return tid
    except Exception as e:
        logger.error(f"Error ensuring tenant for chat {chat_id}: {e}")
        raise

