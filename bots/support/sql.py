# sql.py — async psycopg3 (drop-in statt asyncpg)
import os
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row

# Verbindung aufbauen (jedes Mal neu; für Prod später Pool einführen)
async def get_conn() -> psycopg.AsyncConnection:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL fehlt.")
    # row_factory=dict_row -> Rows direkt als Dicts
    return await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)

# ---------- Users ----------
async def upsert_user(user: Dict[str, Any]):
    async with await get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO support_users (user_id, handle, first_name, last_name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET handle = EXCLUDED.handle,
                              first_name = EXCLUDED.first_name,
                              last_name  = EXCLUDED.last_name
                """,
                (user["user_id"], user.get("username"), user.get("first_name"), user.get("last_name")),
            )
        await conn.commit()

# ---------- Tickets ----------
async def create_ticket(user_id: int, category: str, subject: str, body: str) -> int:
    async with await get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO support_tickets (user_id, category, subject) VALUES (%s,%s,%s) RETURNING id",
                (user_id, category, subject),
            )
            row = await cur.fetchone()
            tid = row["id"]
            await cur.execute(
                "INSERT INTO support_messages (ticket_id, author_user_id, is_public, text) VALUES (%s,%s,TRUE,%s)",
                (tid, user_id, body),
            )
        await conn.commit()
        return tid

async def get_my_tickets(user_id: int, limit: int = 30, tenant_id: int | None = None):
    async with await get_conn() as conn:
        async with conn.cursor() as cur:
            if tenant_id:
                await cur.execute("""
                    SELECT id, category, subject, status, created_at, closed_at
                    FROM support_tickets
                    WHERE user_id=%s AND tenant_id=%s
                    ORDER BY id DESC LIMIT %s
                """, (user_id, tenant_id, limit))
            else:
                await cur.execute("""
                    SELECT id, category, subject, status, created_at, closed_at
                    FROM support_tickets
                    WHERE user_id=%s
                    ORDER BY id DESC LIMIT %s
                """, (user_id, limit))
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_ticket(user_id: int, ticket_id: int):
    async with await get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, user_id, category, subject, status, created_at, closed_at
                FROM support_tickets WHERE id=%s AND user_id=%s
                """,
                (ticket_id, user_id),
            )
            t = await cur.fetchone()
            if not t:
                return None
            await cur.execute(
                """
                SELECT id, author_user_id, is_public, text, attachments, created_at
                FROM support_messages WHERE ticket_id=%s AND is_public=true
                ORDER BY id ASC
                """,
                (ticket_id,),
            )
            msgs = await cur.fetchall()
            return {**dict(t), "messages": [dict(m) for m in msgs]}

async def add_public_message(user_id: int, ticket_id: int, text: str) -> bool:
    async with await get_conn() as conn:
        async with conn.cursor() as cur:
            # Ownership check
            await cur.execute("SELECT user_id FROM support_tickets WHERE id=%s", (ticket_id,))
            row = await cur.fetchone()
            if not row or row["user_id"] != user_id:
                return False
            await cur.execute(
                "INSERT INTO support_messages (ticket_id, author_user_id, is_public, text) VALUES (%s,%s,TRUE,%s)",
                (ticket_id, user_id, text),
            )
        await conn.commit()
        return True

# ---------- KB ----------
async def kb_search(query: str, limit: int = 8):
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
            return [dict(r) for r in rows]

# ---------- MiniApp Settings / Stats ----------
async def save_group_settings(chat_id: int, title: Optional[str], data: dict, updated_by: Optional[int]) -> bool:
    async with await get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO group_settings (chat_id, title, data, updated_by)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (chat_id)
                DO UPDATE SET title=EXCLUDED.title,
                              data=EXCLUDED.data,
                              updated_by=EXCLUDED.updated_by,
                              updated_at=now()
                """,
                (chat_id, title, data, updated_by),
            )
        await conn.commit()
        return True

async def load_group_settings(chat_id: int) -> dict:
    async with await get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT data FROM group_settings WHERE chat_id=%s", (chat_id,))
            row = await cur.fetchone()
            return dict(row["data"]) if row and row.get("data") is not None else {}

async def load_group_stats(chat_id: int, days: int = 14):
    async with await get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT to_char(day_date, 'YYYY-MM-DD') AS date, messages, active, joins, leaves, kicks, reply_p90_ms, spam_actions
                FROM group_daily_agg
                WHERE chat_id=%s AND day_date >= CURRENT_DATE - %s::int
                ORDER BY day_date DESC
                """,
                (chat_id, days),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def resolve_tenant_id_by_chat(chat_id: int) -> int | None:
    async with await get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT tenant_id FROM tenant_groups WHERE chat_id=%s LIMIT 1", (chat_id,))
            row = await cur.fetchone()
            return row["tenant_id"] if row else None

async def ensure_tenant_for_chat(chat_id: int, title: str | None = None, slug: str | None = None) -> int:
    # 1) existiert Mapping?
    tid = await resolve_tenant_id_by_chat(chat_id)
    if tid: return tid
    # 2) neuen Tenant anlegen (wenn slug nicht vorgegeben, generieren)
    gen_slug = (slug or f"tg-{chat_id}").lower()
    async with await get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("INSERT INTO tenants (slug, name) VALUES (%s,%s) ON CONFLICT (slug) DO NOTHING RETURNING id",
                              (gen_slug, title or gen_slug))
            row = await cur.fetchone()
            if row:
                tid = row["id"]
            else:
                # falls parallel angelegt, nachschlagen
                await cur.execute("SELECT id FROM tenants WHERE slug=%s", (gen_slug,))
                tid = (await cur.fetchone())["id"]
            await cur.execute("INSERT INTO tenant_groups (tenant_id, chat_id, title) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                              (tid, chat_id, title))
        await conn.commit()
    return tid
