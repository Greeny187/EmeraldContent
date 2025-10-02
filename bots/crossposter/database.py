
# common/database.py
# Minimaler Async-Pool via psycopg2 für das Crossposter-Projekt.
# Wrapper um psycopg2 mit asyncio-kompatibler API.

import os
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import ThreadedConnectionPool

_pool = None
_executor = ThreadPoolExecutor(max_workers=5)

def _adapt_row(row):
    """Convert psycopg row to asyncpg-like format"""
    if row is None:
        return None
    if isinstance(row, dict):
        return {k: (Json(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()}
    return row

class AsyncPoolWrapper:
    def __init__(self, pool):
        self._pool = pool
        
    async def fetch(self, query, *args):
        def _fetch():
            conn = self._pool.getconn()
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(query, args)
                    return [_adapt_row(row) for row in cur.fetchall()]
            finally:
                self._pool.putconn(conn)
        return await asyncio.get_event_loop().run_in_executor(_executor, _fetch)
    
    async def fetchrow(self, query, *args):
        def _fetchrow():
            conn = self._pool.getconn()
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(query, args)
                    row = cur.fetchone()
                    return _adapt_row(row)
            finally:
                self._pool.putconn(conn)
        return await asyncio.get_event_loop().run_in_executor(_executor, _fetchrow)
    
    async def fetchval(self, query, *args):
        def _fetchval():
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(query, args)
                    return cur.fetchone()[0]
            finally:
                self._pool.putconn(conn)
        return await asyncio.get_event_loop().run_in_executor(_executor, _fetchval)
    
    async def execute(self, query, *args):
        def _execute():
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(query, args)
                conn.commit()
            finally:
                self._pool.putconn(conn)
        return await asyncio.get_event_loop().run_in_executor(_executor, _execute)

async def get_pool():
    """Get a database connection pool with asyncpg-compatible API."""
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL ist nicht gesetzt")
        # Create ThreadedConnectionPool for psycopg2
        pool = ThreadedConnectionPool(minconn=1, maxconn=5, dsn=dsn)
        _pool = AsyncPoolWrapper(pool)
    return _pool
