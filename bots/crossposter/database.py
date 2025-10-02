
import asyncpg, os
_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL ist nicht gesetzt")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    return _pool
