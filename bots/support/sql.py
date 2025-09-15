import os
import asyncpg
from typing import Optional, Dict, Any, List


# Adapter: versuche vorhandenen Pool aus eurer App zu nutzen
try:
    from database import pool as SHARED_POOL # falls ihr schon einen globalen Pool habt
except Exception:
    SHARED_POOL = None


async def get_pool() -> asyncpg.Pool:
    global SHARED_POOL
    if SHARED_POOL is not None:
        return SHARED_POOL
    dsn = os.getenv('DATABASE_URL')
    if not dsn:
        raise RuntimeError('DATABASE_URL fehlt und kein geteilter Pool gefunden.')
    SHARED_POOL = await asyncpg.create_pool(dsn)
    return SHARED_POOL

# --- Users ---
async def upsert_user(user: Dict[str, Any]):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            '''
            INSERT INTO support_users (user_id, handle, first_name, last_name)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id)
            DO UPDATE SET handle=$2, first_name=$3, last_name=$4
            ''',
            user['id'], user.get('username'), user.get('first_name'), user.get('last_name')
        )

# --- Tickets ---
async def create_ticket(user_id: int, category: str, subject: str, body: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        tid = await conn.fetchval(
            'INSERT INTO support_tickets (user_id, category, subject) VALUES ($1,$2,$3) RETURNING id',
            user_id, category, subject
        )
        await conn.execute(
            'INSERT INTO support_messages (ticket_id, author_user_id, is_public, text) VALUES ($1,$2,TRUE,$3)',
            tid, user_id, body
        )
        return tid


async def get_my_tickets(user_id: int, limit: int = 30):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT t.id, t.category, t.subject, t.status, t.created_at, t.closed_at
            FROM support_tickets t
            WHERE t.user_id=$1
            ORDER BY t.id DESC
            LIMIT $2
        ''', user_id, limit)
        return [dict(r) for r in rows]


async def get_ticket(user_id: int, ticket_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        t = await conn.fetchrow('''
            SELECT id, user_id, category, subject, status, created_at, closed_at
            FROM support_tickets WHERE id=$1 AND user_id=$2
        ''', ticket_id, user_id)
        if not t:
            return None
        msgs = await conn.fetch('''
            SELECT id, author_user_id, is_public, text, attachments, created_at
            FROM support_messages WHERE ticket_id=$1 AND is_public=true
            ORDER BY id ASC
        ''', ticket_id)
        return {**dict(t), 'messages': [dict(m) for m in msgs]}


async def add_public_message(user_id: int, ticket_id: int, text: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        # ownership check
        owner = await conn.fetchval('SELECT user_id FROM support_tickets WHERE id=$1', ticket_id)
        if owner != user_id:
            return False
        await conn.execute(
        'INSERT INTO support_messages (ticket_id, author_user_id, is_public, text) VALUES ($1,$2,TRUE,$3)',
        ticket_id, user_id, text
        )
        return True


# --- KB ---
async def kb_search(query: str, limit: int = 8):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            '''SELECT id, title, left(body, 200) AS snippet, tags
            FROM kb_articles
            WHERE title ILIKE '%'||$1||'%' OR body ILIKE '%'||$1||'%'
            ORDER BY score DESC, updated_at DESC
            LIMIT $2''', query, limit
        )
        return [dict(r) for r in rows]