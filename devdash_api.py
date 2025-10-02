import os, hmac, hashlib, time, asyncio
from typing import Tuple, Dict, Any, List, Optional
from aiohttp import web
import psycopg
from psycopg_pool import ConnectionPool

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise RuntimeError("DATABASE_URL ist nicht gesetzt")

SECRET_KEY = os.getenv("SECRET_KEY", "change-me")  # setze in Heroku!
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
BOT_TOKEN = os.getenv("BOT1_TOKEN") or os.getenv("BOT_TOKEN")  # dein Haupt-Bot-Token fürs Login-Verify

pool = ConnectionPool(DB_URL, min_size=1, max_size=5, kwargs={"autocommit": True})

def _allow_origin(origin: Optional[str]) -> str:
    if not origin or "*" in ALLOWED_ORIGINS:
        return "*"
    return origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]

def _cors_headers(request: web.Request) -> Dict[str, str]:
    origin = request.headers.get("Origin")
    allow_origin = _allow_origin(origin)
    return {
        "Access-Control-Allow-Origin": allow_origin,
        "Access-Control-Allow-Headers": "*, Authorization, Content-Type",
        "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
    }

async def _to_thread(func, *a, **kw):
    return await asyncio.to_thread(func, *a, **kw)

# ---------- DB helpers (sync, in Thread) ----------
def _fetch(sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    with pool.connection() as con, con.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description] if cur.description else []
        rows = cur.fetchall() if cur.description else []
        return [dict(zip(cols, r)) for r in rows]

def _fetchrow(sql: str, params: Tuple = ()) -> Optional[Dict[str, Any]]:
    with pool.connection() as con, con.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row: return None
        cols = [c.name for c in cur.description]
        return dict(zip(cols, row))

def _execute(sql: str, params: Tuple = ()) -> None:
    with pool.connection() as con, con.cursor() as cur:
        cur.execute(sql, params)

async def fetch(sql: str, params: Tuple = ()): return await _to_thread(_fetch, sql, params)
async def fetchrow(sql: str, params: Tuple = ()): return await _to_thread(_fetchrow, sql, params)
async def execute(sql: str, params: Tuple = ()): return await _to_thread(_execute, sql, params)

# ---------- bootstrap tables ----------
INIT_SQL = """
create table if not exists dashboard_users (
  telegram_id bigint primary key,
  username text,
  first_name text,
  last_name text,
  photo_url text,
  role text not null default 'dev',
  tier text not null default 'pro',
  created_at timestamp not null default now(),
  updated_at timestamp not null default now()
);
create table if not exists dashboard_bots (
  id serial primary key,
  name text not null,
  slug text not null unique,
  description text,
  is_active boolean not null default true,
  created_at timestamp not null default now(),
  updated_at timestamp not null default now()
);
create table if not exists dashboard_ads (
  id serial primary key,
  name text not null,
  placement text not null check (placement in ('header','sidebar','in-bot','story','inline')),
  content text not null,
  is_active boolean not null default true,
  start_at timestamp,
  end_at timestamp,
  targeting jsonb not null default '{}'::jsonb,
  bot_slug text,
  created_at timestamp not null default now(),
  updated_at timestamp not null default now()
);
create table if not exists dashboard_feature_flags (
  key text primary key,
  value jsonb not null,
  description text
);
"""

async def ensure_tables():
    for stmt in [s.strip() for s in INIT_SQL.split(";") if s.strip()]:
        await execute(stmt + ";")

# ---------- token (ohne PyJWT) ----------
def create_token(user_id: int, ttl_sec: int = 7*24*3600) -> str:
    exp = int(time.time()) + ttl_sec
    payload = f"{user_id}.{exp}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"

def verify_token(token: str) -> int:
    try:
        user_id_s, exp_s, sig = token.split(".")
        payload = f"{user_id_s}.{exp_s}"
        check = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if check != sig: raise ValueError("bad signature")
        if time.time() > int(exp_s): raise ValueError("expired")
        return int(user_id_s)
    except Exception as e:
        raise ValueError(f"invalid token: {e}")

# ---------- telegram login verify ----------
def verify_telegram_auth(auth: Dict[str, Any]) -> Dict[str, Any]:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN/BOT1_TOKEN env fehlt")
    for r in ["id","auth_date","hash"]:
        if r not in auth: raise ValueError(f"missing {r}")
    secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    data_check = "\n".join(f"{k}={auth[k]}" for k in sorted([k for k in auth if k != "hash"]))
    calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if calc != auth["hash"]: raise ValueError("bad hash")
    # 24h gültig
    if time.time() - int(auth["auth_date"]) > int(os.getenv("TELEGRAM_LOGIN_TTL_SECONDS", "86400")):
        raise ValueError("login expired")
    return {
        "id": int(auth["id"]),
        "username": auth.get("username"),
        "first_name": auth.get("first_name"),
        "last_name": auth.get("last_name"),
        "photo_url": auth.get("photo_url"),
    }

# ---------- helpers ----------
def _json(data: Any, request: web.Request, status: int = 200):
    resp = web.json_response(data, status=status)
    for k,v in _cors_headers(request).items():
        resp.headers[k] = v
    return resp

async def _auth_user(request: web.Request) -> int:
    auth = request.headers.get("Authorization","")
    if not auth.lower().startswith("bearer "):
        raise web.HTTPUnauthorized(text="Missing bearer token")
    token = auth.split(" ",1)[1].strip()
    try:
        return verify_token(token)
    except Exception as e:
        raise web.HTTPUnauthorized(text=str(e))

# ---------- routes ----------
async def options_handler(request):
    return web.Response(status=200, headers={
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, X-Telegram-Init-Data'
    })

async def healthz(request: web.Request):
    return _json({"status":"ok","time":int(time.time())}, request)

async def auth_telegram(request: web.Request):
    payload = await request.json()
    user = verify_telegram_auth(payload)
    await execute(
        """insert into dashboard_users(telegram_id,username,first_name,last_name,photo_url)
           values(%s,%s,%s,%s,%s)
           on conflict(telegram_id) do update set
             username=excluded.username, first_name=excluded.first_name,
             last_name=excluded.last_name, photo_url=excluded.photo_url, updated_at=now()""",
        (user["id"], user.get("username"), user.get("first_name"), user.get("last_name"), user.get("photo_url"))
    )
    row = await fetchrow("select role,tier from dashboard_users where telegram_id=%s", (user["id"],))
    token = create_token(user["id"])
    return _json({"access_token": token, "token_type":"bearer", "role": row["role"] if row else "dev", "tier": row["tier"] if row else "pro"}, request)

async def me(request: web.Request):
    user_id = await _auth_user(request)
    row = await fetchrow("select username, role, tier, first_name, last_name, photo_url from dashboard_users where telegram_id=%s", (user_id,))
    return _json({"user_id": user_id, "profile": row}, request)

async def overview(request: web.Request):
    await _auth_user(request)
    def cnt(sql): 
        try: return _fetchrow(sql).get("c",0)
        except: return 0
    users_total = await _to_thread(lambda: cnt("select count(1) as c from dashboard_users"))
    ads_active = await _to_thread(lambda: cnt("select count(1) as c from dashboard_ads where is_active=true"))
    bots_active = await _to_thread(lambda: cnt("select count(1) as c from dashboard_bots where is_active=true"))
    return _json({"users_total":users_total,"ads_active":ads_active,"bots_active":bots_active}, request)

async def bots_list(request: web.Request):
    await _auth_user(request)
    rows = await fetch("select id, name, slug, description, is_active from dashboard_bots order by id asc")
    return _json(rows, request)

async def bots_add(request: web.Request):
    await _auth_user(request)
    body = await request.json()
    name = body.get("name"); slug = body.get("slug"); desc = body.get("description"); is_active = bool(body.get("is_active", True))
    if not name or not slug: raise web.HTTPBadRequest(text="name and slug required")
    row = await fetchrow(
        "insert into dashboard_bots(name,slug,description,is_active) values(%s,%s,%s,%s) returning id,name,slug,description,is_active",
        (name, slug, desc, is_active)
    )
    return _json(row, request, status=201)

def register_devdash_routes(app: web.Application):
    
    # CORS Preflight für alle /devdash/* Pfade
    app.router.add_route("OPTIONS", "/devdash/{tail:.*}", options_handler)

    # DevDash API
    app.router.add_get ("/devdash/healthz",           healthz)
    app.router.add_post("/devdash/auth/telegram",     auth_telegram)
    app.router.add_get ("/devdash/me",                me)
    app.router.add_get ("/devdash/metrics/overview",  overview)
    app.router.add_get ("/devdash/bots",              bots_list)
    app.router.add_post("/devdash/bots",              bots_add)

