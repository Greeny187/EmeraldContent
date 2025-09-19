
# bots/content/crossposter_models.py
# Kapselt DB-Zugriffe (mandantenfähig).

from typing import List, Dict, Any, Optional
from .database import get_pool

async def user_in_tenant(tenant_id: int, user_id: int) -> bool:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT 1 FROM tenant_members WHERE tenant_id=$1 AND user_id=$2", tenant_id, user_id)
    return bool(row)

async def list_tenants_for_user(user_id: int):
    pool = await get_pool()
    return await pool.fetch(        "SELECT t.id, t.name, t.slug FROM tenants t JOIN tenant_members m ON m.tenant_id=t.id WHERE m.user_id=$1 ORDER BY t.id DESC",        user_id    )

async def create_route(tenant_id: int, owner_user_id: int, source_chat_id: int, destinations: List[Dict], transform: Dict, filters: Dict, active: bool):
    pool = await get_pool()
    return await pool.fetchrow(        """        INSERT INTO crossposter_routes (tenant_id, owner_user_id, source_chat_id, destinations, transform, filters, active)        VALUES ($1,$2,$3,$4,$5,$6,$7)        RETURNING *        """,        tenant_id, owner_user_id, source_chat_id, destinations, transform, filters, active    )

async def update_route(route_id: int, tenant_id: int, owner_user_id: int, source_chat_id: int, destinations: List[Dict], transform: Dict, filters: Dict, active: bool):
    pool = await get_pool()
    return await pool.fetchrow(        """        UPDATE crossposter_routes SET          source_chat_id=$1, destinations=$2, transform=$3, filters=$4, active=$5, updated_at=NOW()        WHERE id=$6 AND tenant_id=$7 AND owner_user_id=$8        RETURNING *        """,        source_chat_id, destinations, transform, filters, active, route_id, tenant_id, owner_user_id    )

async def delete_route(route_id: int, tenant_id: int, owner_user_id: int):
    pool = await get_pool()
    await pool.execute("DELETE FROM crossposter_routes WHERE id=$1 AND tenant_id=$2 AND owner_user_id=$3", route_id, tenant_id, owner_user_id)

async def list_routes(tenant_id: int, owner_user_id: int):
    pool = await get_pool()
    return await pool.fetch(        "SELECT id, source_chat_id, destinations, transform, filters, active FROM crossposter_routes WHERE tenant_id=$1 AND owner_user_id=$2 ORDER BY id DESC",        tenant_id, owner_user_id    )

async def stats(tenant_id: int, owner_user_id: int):
    pool = await get_pool()
    total_routes = await pool.fetchval("SELECT COUNT(*) FROM crossposter_routes WHERE tenant_id=$1 AND owner_user_id=$2", tenant_id, owner_user_id)
    last_logs = await pool.fetch(        """        SELECT route_id, status, COUNT(*) AS n        FROM crossposter_logs l        JOIN crossposter_routes r ON r.id = l.route_id        WHERE r.tenant_id=$1 AND r.owner_user_id=$2        GROUP BY route_id, status        ORDER BY route_id        """,        tenant_id, owner_user_id    )
    return int(total_routes), [dict(x) for x in last_logs]
