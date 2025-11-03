from psycopg_pool import ConnectionPool
import os

DB_URL = os.getenv("DATABASE_URL")
pool = ConnectionPool(DB_URL, min_size=1, max_size=5, kwargs={"autocommit": True})

def execute(sql: str, params: tuple = ()):
    with pool.connection() as con, con.cursor() as cur:
        cur.execute(sql, params)

def fetch(sql: str, params: tuple = ()):
    with pool.connection() as con, con.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description] if cur.description else []
        rows = cur.fetchall() if cur.description else []
        return [dict(zip(cols, r)) for r in rows]

def fetchrow(sql: str, params: tuple = ()):
    with pool.connection() as con, con.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row: return None
        cols = [c.name for c in cur.description]
        return dict(zip(cols, row))
