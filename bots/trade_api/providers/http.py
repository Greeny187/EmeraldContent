import aiohttp
from contextlib import asynccontextmanager

@asynccontextmanager
async def session():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        yield s
