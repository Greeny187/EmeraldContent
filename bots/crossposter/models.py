
from typing import List, Dict, Any
from bots.crossposter.database import get_pool

async def user_in_tenant(tenant_id: int, user_id: int) -> bool:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT 1 FROM tenant_members WHERE tenant_id=$1 AND user_id=$2", tenant_id, user_id)
    return bool(row)

async def list_tenants_for_user(user_id: int):
    pool = await get_pool()
    return await pool.fetch("SELECT t.id, t.name, t.slug FROM tenants t JOIN tenant_members m ON m.tenant_id=t.id WHERE m.user_id=$1 ORDER BY t.id DESC", user_id)

async def ensure_default_tenant_for_user(user: dict) -> dict:
    pool = await get_pool()
    uid = int(user.get("id"))
    username = user.get("username") or (str(user.get("first_name","user")).lower())
    slug = f"t{uid}"
    trow = await pool.fetchrow("SELECT id, name, slug FROM tenants WHERE slug=$1", slug)
    if not trow:
        trow = await pool.fetchrow("INSERT INTO tenants (name, slug) VALUES ($1,$2) RETURNING id, name, slug", f"{username}-workspace", slug)
    await pool.execute("INSERT INTO tenant_members (tenant_id, user_id, role) VALUES ($1,$2,'owner') ON CONFLICT (tenant_id, user_id) DO NOTHING", trow["id"], uid)
    return trow

async def create_tenant_for_user(user_id: int, name: str, slug: str) -> dict:
    pool = await get_pool()
    row = await pool.fetchrow("INSERT INTO tenants (name, slug) VALUES ($1,$2) RETURNING id, name, slug", name, slug)
    await pool.execute("INSERT INTO tenant_members (tenant_id, user_id, role) VALUES ($1,$2,'owner') ON CONFLICT (tenant_id, user_id) DO NOTHING", row["id"], user_id)
    return row

async def create_route(tenant_id: int, owner_user_id: int, source_chat_id: int, destinations: List[Dict], transform: Dict, filters: Dict, active: bool):
    pool = await get_pool()
    return await pool.fetchrow(
        "INSERT INTO crossposter_routes (tenant_id, owner_user_id, source_chat_id, destinations, transform, filters, active) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *",
        tenant_id, owner_user_id, source_chat_id, destinations, transform, filters, active
    )

async def update_route(route_id: int, tenant_id: int, owner_user_id: int, source_chat_id: int, destinations: List[Dict], transform: Dict, filters: Dict, active: bool):
    pool = await get_pool()
    return await pool.fetchrow(
        "UPDATE crossposter_routes SET source_chat_id=$1, destinations=$2, transform=$3, filters=$4, active=$5, updated_at=NOW() WHERE id=$6 AND tenant_id=$7 AND owner_user_id=$8 RETURNING *",
        source_chat_id, destinations, transform, filters, active, route_id, tenant_id, owner_user_id
    )

async def delete_route(route_id: int, tenant_id: int, owner_user_id: int):
    pool = await get_pool()
    await pool.execute("DELETE FROM crossposter_routes WHERE id=$1 AND tenant_id=$2 AND owner_user_id=$3", route_id, tenant_id, owner_user_id)

async def list_routes(tenant_id: int, owner_user_id: int):
    pool = await get_pool()
    return await pool.fetch("SELECT id, source_chat_id, destinations, transform, filters, active FROM crossposter_routes WHERE tenant_id=$1 AND owner_user_id=$2 ORDER BY id DESC", tenant_id, owner_user_id)

async def stats(tenant_id: int, owner_user_id: int):
    pool = await get_pool()
    total_routes = await pool.fetchval("SELECT COUNT(*) FROM crossposter_routes WHERE tenant_id=$1 AND owner_user_id=$2", tenant_id, owner_user_id)
    last_logs = await pool.fetch(
        "SELECT route_id, status, COUNT(*) AS n FROM crossposter_logs l JOIN crossposter_routes r ON r.id = l.route_id WHERE r.tenant_id=$1 AND r.owner_user_id=$2 GROUP BY route_id, status ORDER BY route_id",
        tenant_id, owner_user_id
    )
    return int(total_routes), [dict(x) for x in last_logs]

# Logs
async def get_logs(tenant_id: int, route_id: int = None, status: str = None, limit: int = 50):
    pool = await get_pool()
    query = "SELECT id, route_id, status, dest_descriptor, error, created_at FROM crossposter_logs WHERE tenant_id=$1"
    params = [tenant_id]
    if route_id:
        query += f" AND route_id=${len(params)+1}"
        params.append(route_id)
    if status:
        query += f" AND status=${len(params)+1}"
        params.append(status)
    query += f" ORDER BY created_at DESC LIMIT {limit}"
    return await pool.fetch(query, *params)

async def log_event(tenant_id: int, route_id: int, source_chat_id: int, source_message_id: int, dest_descriptor: Dict, status: str, error: str = None, dedup_hash: str = None):
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO crossposter_logs (tenant_id, route_id, source_chat_id, source_message_id, dest_descriptor, status, error, dedup_hash) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        tenant_id, route_id, source_chat_id, source_message_id, dest_descriptor, status, error, dedup_hash
    )

# Connectors
async def upsert_connector(tenant_id: int, type_: str, label: str, config: dict, active: bool = True):
    pool = await get_pool()
    row = await pool.fetchrow(
        "UPDATE connectors SET label=$3, config=$4, active=$5, updated_at=NOW() WHERE tenant_id=$1 AND type=$2 RETURNING *",
        tenant_id, type_, label, config, active
    )
    if not row:
        row = await pool.fetchrow(
            "INSERT INTO connectors (tenant_id, type, label, config, active) VALUES ($1,$2,$3,$4,$5) RETURNING *",
            tenant_id, type_, label, config, active
        )
    return row

async def get_connector(tenant_id: int, type_: str):
    pool = await get_pool()
    return await pool.fetchrow("SELECT * FROM connectors WHERE tenant_id=$1 AND type=$2 AND active=TRUE ORDER BY id DESC LIMIT 1", tenant_id, type_)

async def list_connectors(tenant_id: int):
    pool = await get_pool()
    return await pool.fetch("SELECT id, type, label, active FROM connectors WHERE tenant_id=$1 ORDER BY type, id DESC", tenant_id)

# Validate route before processing
async def get_route(route_id: int):
    pool = await get_pool()
    return await pool.fetchrow("SELECT * FROM crossposter_routes WHERE id=$1", route_id)

async def is_route_active(route_id: int) -> bool:
    pool = await get_pool()
    return await pool.fetchval("SELECT active FROM crossposter_routes WHERE id=$1", route_id) or False

