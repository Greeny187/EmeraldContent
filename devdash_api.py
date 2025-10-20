import os, hmac, hashlib, time, asyncio, logging, base64, secrets, json
import re, httpx
from typing import Tuple, Dict, Any, List, Optional
from aiohttp import web
from psycopg_pool import ConnectionPool
from decimal import Decimal, getcontext
from jwt_tools import create_token as jwt_create_token, decode_token as jwt_decode_token

try:
    from nacl.signing import VerifyKey
    from nacl.exceptions import BadSignatureError
except Exception:  # optional; we only raise if verify is actually used
    VerifyKey = None
    BadSignatureError = Exception

getcontext().prec = 40

log = logging.getLogger("devdash")

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise RuntimeError("DATABASE_URL ist nicht gesetzt")

SECRET_KEY = os.getenv("SECRET_KEY", "change-me")  # setze in Heroku!DEV_LOGIN_CODE = os.getenv("DEV_LOGIN_CODE")
DEV_LOGIN_CODE = os.getenv("DEV_LOGIN_CODE")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
BOT_TOKEN = os.getenv("BOT1_TOKEN") or os.getenv("BOT_TOKEN")  # Bot‑Token für Telegram-Login Verify

# NEAR config
NEAR_NETWORK = os.getenv("NEAR_NETWORK", "mainnet")  # "mainnet" | "testnet"
NEAR_RPC_URL = os.getenv("NEAR_RPC_URL", "https://rpc.mainnet.near.org")
NEAR_TOKEN_CONTRACT = os.getenv("NEAR_TOKEN_CONTRACT", "")  # z.B. token.emeraldcontent.near
NEARBLOCKS_API = os.getenv("NEARBLOCKS_API", "https://api.nearblocks.io")
TON_API_BASE   = os.getenv("TON_API_BASE", "https://tonapi.io")
TON_API_KEY    = os.getenv("TON_API_KEY", "")

pool = ConnectionPool(DB_URL, min_size=1, max_size=5, kwargs={"autocommit": True})

# ------------------------------ helpers ------------------------------

@web.middleware
async def cors_middleware(request, handler):
    try:
        resp = await handler(request)
    except web.HTTPException as ex:
        resp = ex
    except Exception as ex:
        resp = web.Response(status=500, text=str(ex))
    for k, v in _cors_headers(request).items():
        resp.headers[k] = v
    return resp

def _allow_origin(origin: Optional[str]) -> str:
    if not origin or "*" in ALLOWED_ORIGINS:
        return "*"
    return origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]

# -------- Dev-Login (Code+Telegram-ID) für dich, liefert JWT --------
async def dev_login(request: web.Request):
    body = await request.json()
    code = (body.get("code") or "").strip()
    tg_id = int(body.get("telegram_id") or 0)
    if not tg_id:
        raise web.HTTPBadRequest(text="telegram_id required")
    expected = os.getenv("DEV_LOGIN_CODE", "")
    if not expected or code != expected:
        raise web.HTTPUnauthorized(text="bad dev code")
    await execute("""
      insert into dashboard_users(telegram_id, username, role, tier)
      values (%s, %s, 'dev', 'pro')
      on conflict (telegram_id) do update set
        username=coalesce(excluded.username, dashboard_users.username),
        updated_at=now()
    """, (tg_id, body.get("username")))
    tok = _jwt_issue(tg_id, role="dev", tier="pro")
    return _json({"access_token": tok, "token_type": "bearer"}, request)

def _cors_headers(request: web.Request) -> Dict[str, str]:
    origin = request.headers.get("Origin")
    allow_origin = _allow_origin(origin)
    return {
        "Access-Control-Allow-Origin": allow_origin,
        "Access-Control-Allow-Headers": "*, Authorization, Content-Type",
        "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
    }

def yocto_to_near_str(yocto: str) -> str:
    try:
        return str(Decimal(yocto) / Decimal(10**24))
    except Exception:
        return "0"

async def _rpc_view_account_near(account_id: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        payload = {
            "jsonrpc":"2.0","id":"view_account","method":"query",
            "params":{"request_type":"view_account","finality":"final","account_id":account_id}
        }
        r = await client.post(NEAR_RPC_URL, json=payload)
        r.raise_for_status()
        return r.json()["result"]

async def near_account_overview(request: web.Request):
    await _auth_user(request)
    account_id = request.query.get("account_id")
    if not account_id:
        raise web.HTTPBadRequest(text="account_id required")
    tokens_csv = request.query.get("tokens","").strip()
    tokens = [t for t in tokens_csv.split(",") if t]

    acct = await _rpc_view_account_near(account_id)
    out = {
        "account_id": account_id,
        "near": {
            "amount_yocto": acct.get("amount","0"),
            "amount_near":  yocto_to_near_str(acct.get("amount","0")),
            "locked_yocto": acct.get("locked","0"),
            "locked_near":  yocto_to_near_str(acct.get("locked","0")),
            "storage_usage": acct.get("storage_usage", 0),
            "code_hash": acct.get("code_hash")
        },
        "tokens": {}
    }
    for c in tokens:
        try:
            bal = await _rpc_view_function(c, "ft_balance_of", {"account_id": account_id})
        except Exception as e:
            bal = {"error": str(e)}
        out["tokens"][c] = bal
    return _json(out, request)

async def set_ton_address(request: web.Request):
    user_id = await _auth_user(request)
    body = await request.json()
    address = (body.get("address") or "").strip()
    await execute("update dashboard_users set ton_address=%s, updated_at=now() where telegram_id=%s",
                  (address, user_id))
    return _json({"ok": True, "ton_address": address}, request)

async def wallets_overview(request: web.Request):
    # Liefert Watch-Accounts (NEAR/TON) + eigene TON-Adresse (aus dashboard_users)
    user_id = await _auth_user(request)
    me = await fetchrow("select near_account_id, ton_address from dashboard_users where telegram_id=%s", (user_id,))
    watches = await fetch("select id,chain,account_id,label,meta,created_at from dashboard_watch_accounts order by id asc")
    return _json({"me": me, "watch": watches}, request)

async def _to_thread(func, *a, **kw):
    return await asyncio.to_thread(func, *a, **kw)


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
        if not row:
            return None
        cols = [c.name for c in cur.description]
        return dict(zip(cols, row))


def _execute(sql: str, params: Tuple = ()) -> None:
    with pool.connection() as con, con.cursor() as cur:
        cur.execute(sql, params)


async def fetch(sql: str, params: Tuple = ()): return await _to_thread(_fetch, sql, params)
async def fetchrow(sql: str, params: Tuple = ()): return await _to_thread(_fetchrow, sql, params)
async def execute(sql: str, params: Tuple = ()): return await _to_thread(_execute, sql, params)


# --------------------------- bootstrap tables ---------------------------
INIT_SQL = """
create table if not exists dashboard_users (
  telegram_id bigint primary key,
  username text,
  first_name text,
  last_name text,
  photo_url text,
  role text not null default 'dev',
  tier text not null default 'pro',
  -- NEAR binding
  near_account_id text,
  near_public_key text,
  near_connected_at timestamp,
  created_at timestamp not null default now(),
  updated_at timestamp not null default now()
);

create table if not exists dashboard_bots (
   id bigserial primary key,
   username       text not null unique,
   title          text,
   env_token_key  text not null,
   is_active      boolean not null default true,
   meta           jsonb default '{}'::jsonb,
   created_at     timestamptz not null default now(),
   updated_at     timestamptz not null default now()
);

-- Registry for fan‑out to each bot (mesh)
create table if not exists dashboard_bot_endpoints (
  id serial primary key,
  bot_slug text not null references dashboard_bots(slug) on delete cascade,
  base_url text not null,
  api_key text,
  metrics_path text not null default '/internal/metrics',
  health_path  text not null default '/internal/health',
  is_active boolean not null default true,
  last_seen timestamp,
  notes text,
  unique(bot_slug, base_url)
);

-- Ads/FeatureFlags stay as before (might already exist in your DB)
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

-- Login challenges for NEAR signature binding
create table if not exists dashboard_nonces (
  telegram_id bigint not null,
  nonce bytea not null,
  created_at timestamp not null default now(),
  primary key(telegram_id)
);

-- Off‑chain token accounting (optional, to complement on‑chain data)
create table if not exists dashboard_token_events (
  id serial primary key,
  happened_at timestamp not null default now(),
  kind text not null check (kind in ('mint','burn','reward','fee','redeem','manual')),
  amount numeric(36, 18) not null,
  unit text not null default 'EMRLD',
  actor_telegram_id bigint,
  ref jsonb,
  note text
);
"""

async def ensure_tables():
    # Basis-Schema (bestehendes INIT_SQL)
    for stmt in [s.strip() for s in INIT_SQL.split(";") if s.strip()]:
        await execute(stmt + ";")
    # --- Migrations: TON + Watchlist (idempotent) ---
    await execute("alter table if exists dashboard_users add column if not exists ton_address text;")
    await execute("""
        create table if not exists dashboard_watch_accounts (
            id serial primary key,
            chain text not null check (chain in ('near','ton')),
            account_id text not null,
            label text,
            meta jsonb default '{}'::jsonb,
            created_at timestamp not null default now(),
            unique(chain, account_id)
         );
    """)
    await execute("""
        insert into dashboard_watch_accounts(chain, account_id, label)
        values ('near','emeraldcontent.near','Main Wallet'),
            ('near','pay.emeraldcontent.near','Payments')
        on conflict do nothing;
    """)

async def _telegram_getme(token: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as cx:
        r = await cx.get(f"https://api.telegram.org/bot{token}/getMe")
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"getMe failed: {data}")
        return data["result"]

async def scan_env_bots() -> int:
    """finde ENV-Variablen wie BOT1_TOKEN / BOT_XYZ_TOKEN, hole getMe und upserte in dashboard_bots"""
    added = 0
    for k, v in os.environ.items():
        if not re.fullmatch(r"BOT[A-Z0-9_]*_TOKEN", k):
            continue
        token = v.strip()
        if not token:
            continue
        try:
            me = await _telegram_getme(token)
            username = me.get("username") or me.get("first_name") or k
            title = me.get("first_name") or username
            await execute("""
              insert into dashboard_bots(username, title, env_token_key, is_active, meta)
              values (%s,%s,%s,true, %s::jsonb)
              on conflict(username) do update set
                title=excluded.title,
                env_token_key=excluded.env_token_key,
                updated_at=now()
            """, (username, title, k, json.dumps({"id": me.get("id")})))
            added += 1
        except Exception as e:
            logging.warning("scan_env_bots: %s -> %s", k, e)
    return added

# ------------------------------ tokens (JWT) ------------------------------

def _jwt_issue(telegram_id: int, role: str = "dev", tier: str = "pro") -> str:
    # Standardisierte JWTs wie in jwt_tools.py / FastAPI
    return jwt_create_token({"sub": str(telegram_id), "role": role, "tier": tier})

def _jwt_verify(token: str) -> int:
    data = jwt_decode_token(token)   # wirft bei Ungültigkeit
    return int(data.get("sub"))


# ----------------------- Telegram login verify -----------------------

def verify_telegram_auth(auth: Dict[str, Any]) -> Dict[str, Any]:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN/BOT1_TOKEN env fehlt")
    for r in ["id", "auth_date", "hash"]:
        if r not in auth:
            raise ValueError(f"missing {r}")
    secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    data_check = "\n".join(f"{k}={auth[k]}" for k in sorted([k for k in auth if k != "hash"]))
    calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if calc != auth["hash"]:
        raise ValueError("bad hash")
    if time.time() - int(auth["auth_date"]) > int(os.getenv("TELEGRAM_LOGIN_TTL_SECONDS", "86400")):
        raise ValueError("login expired")
    return {
        "id": int(auth["id"]),
        "username": auth.get("username"),
        "first_name": auth.get("first_name"),
        "last_name": auth.get("last_name"),
        "photo_url": auth.get("photo_url"),
    }


# ------------------------------ utils ------------------------------

def _json(data: Any, request: web.Request, status: int = 200):
    resp = web.json_response(data, status=status)
    for k, v in _cors_headers(request).items():
        resp.headers[k] = v
    return resp


async def _auth_user(request: web.Request) -> int:
    auth = request.headers.get("Authorization","")
    if not auth.lower().startswith("bearer "):
        raise web.HTTPUnauthorized(text="Missing bearer token")
    token = auth.split(" ",1)[1].strip()

    # 1) HMAC-Token (user_id.exp.sig)
    try:
        return _jwt_verify(token)
    except Exception as e:
        raise web.HTTPUnauthorized(text=f"invalid token: {e}")

# ------------------------------ base58 ------------------------------
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58_INDEX[ch]
    # convert to bytes
    full = n.to_bytes((n.bit_length() + 7) // 8, "big") or b"\x00"
    # handle leading zeros
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + full


# ------------------------------ routes ------------------------------
async def options_handler(request):
    headers = _cors_headers(request)
    return web.Response(status=204, headers=headers)


async def healthz(request: web.Request):
    return _json({"status": "ok", "time": int(time.time())}, request)


async def auth_telegram(request: web.Request):
    log.info("auth_telegram hit from origin=%s ua=%s", request.headers.get("Origin"), request.headers.get("User-Agent"))
    payload = await request.json()
    try:
        user = verify_telegram_auth(payload)
    except Exception as e:
        return _json({"error": str(e)}, request, status=400)
    await execute(
        """
        insert into dashboard_users(telegram_id,username,first_name,last_name,photo_url)
        values(%s,%s,%s,%s,%s)
        on conflict(telegram_id) do update set
          username=excluded.username,
          first_name=excluded.first_name,
          last_name=excluded.last_name,
          photo_url=excluded.photo_url,
          updated_at=now()
        """,
        (user["id"], user.get("username"), user.get("first_name"), user.get("last_name"), user.get("photo_url")),
    )
    row = await fetchrow("select role,tier from dashboard_users where telegram_id=%s", (user["id"],))
    token = _jwt_issue(user["id"], role=row["role"] if row else "dev", tier=row["tier"] if row else "pro")
    return _json(
        {
            "access_token": token,
            "token_type": "bearer",
            "role": row["role"] if row else "dev",
            "tier": row["tier"] if row else "pro",
        },
        request,
    )


async def me(request: web.Request):
    user_id = await _auth_user(request)
    row = await fetchrow(
        "select username, role, tier, first_name, last_name, photo_url, "
        "near_account_id, near_public_key, near_connected_at, ton_address "
        "from dashboard_users where telegram_id=%s",
        (user_id,)
    )
    return _json({"user_id": user_id, "profile": row}, request)


async def overview(request: web.Request):
    await _auth_user(request)

    def cnt(sql):
        try:
            r = _fetchrow(sql)
            return (r or {}).get("c", 0)
        except Exception:
            return 0

    users_total = await _to_thread(lambda: cnt("select count(1) as c from dashboard_users"))
    ads_active = await _to_thread(lambda: cnt("select count(1) as c from dashboard_ads where is_active=true"))
    bots_active = await _to_thread(lambda: cnt("select count(1) as c from dashboard_bots where is_active=true"))
    return _json({"users_total": users_total, "ads_active": ads_active, "bots_active": bots_active}, request)


async def bots_list(request: web.Request):
    await _auth_user(request)
    rows = await fetch("select id, username, title, env_token_key, is_active, meta, created_at, updated_at from dashboard_bots order by id asc")
    return _json({"bots": rows}, request)

async def bots_refresh(request: web.Request):
    await _auth_user(request)
    added = await scan_env_bots()
    rows = await fetch("select id, username, title, env_token_key, is_active, meta, created_at, updated_at from dashboard_bots order by id asc")
    return _json({"refreshed": added, "bots": rows}, request)

async def bots_add(request: web.Request):
    await _auth_user(request)
    body = await request.json()
    name = body.get("name")
    slug = body.get("slug")
    desc = body.get("description")
    is_active = bool(body.get("is_active", True))
    if not name or not slug:
        raise web.HTTPBadRequest(text="name and slug required")
    row = await fetchrow(
        "insert into dashboard_bots(name,slug,description,is_active) values(%s,%s,%s,%s) returning id,name,slug,description,is_active",
        (name, slug, desc, is_active),
    )
    return _json(row, request, status=201)


# ------------------------------ NEAR Connect ------------------------------
async def near_challenge(request: web.Request):
    user_id = await _auth_user(request)
    nonce = secrets.token_bytes(32)
    # one active nonce per user (replaced on each request)
    await execute(
        "insert into dashboard_nonces(telegram_id, nonce) values(%s,%s) on conflict (telegram_id) do update set nonce=excluded.nonce, created_at=now()",
        (user_id, nonce),
    )
    message = f"Login to Emerald DevDash — tg:{user_id}"  # human‑readable tag
    return _json(
        {
            "network": NEAR_NETWORK,
            "recipient": "emerald.dev",  # any domain/app tag; not on‑chain
            "nonce_b64": base64.b64encode(nonce).decode(),
            "message": message,
        },
        request,
    )


async def near_verify(request: web.Request):
    user_id = await _auth_user(request)
    body = await request.json()
    account_id = body.get("account_id")
    public_key = body.get("public_key")  # e.g. "ed25519:..." base58
    sig_b64 = body.get("signature_b64")
    nonce_b64 = body.get("nonce_b64")
    message = body.get("message")

    if not (account_id and public_key and sig_b64 and nonce_b64 and message):
        raise web.HTTPBadRequest(text="missing fields")

    row = await fetchrow("select nonce from dashboard_nonces where telegram_id=%s", (user_id,))
    if not row:
        raise web.HTTPBadRequest(text="no challenge")
    expected_nonce = row["nonce"]  # bytes

    if base64.b64encode(expected_nonce).decode() != nonce_b64:
        raise web.HTTPBadRequest(text="nonce mismatch")

    # verify ed25519 signature against message bytes (NEP‑413 compatible wallets sign a canonical payload; most also accept raw message)
    if VerifyKey is None:
        raise web.HTTPBadRequest(text="pynacl not installed on server")

    try:
        if public_key.startswith("ed25519:"):
            pk_raw = b58decode(public_key.split(":", 1)[1])
        else:
            pk_raw = b58decode(public_key)
        verify_key = VerifyKey(pk_raw)
        verify_key.verify(message.encode(), base64.b64decode(sig_b64))
    except BadSignatureError:
        raise web.HTTPBadRequest(text="bad signature")
    except Exception as e:
        raise web.HTTPBadRequest(text=f"verify error: {e}")

    # (Optional) sanity‑check that public key belongs to account via RPC (access key list)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            payload = {
                "jsonrpc": "2.0",
                "id": "verify-key",
                "method": "query",
                "params": {
                    "request_type": "view_access_key_list",
                    "finality": "final",
                    "account_id": account_id,
                },
            }
            r = await client.post(NEAR_RPC_URL, json=payload)
            r.raise_for_status()
            keys = [k.get("public_key") for k in r.json().get("result", {}).get("keys", [])]
            if public_key not in keys:
                log.warning("public key not in access_key_list for %s", account_id)
    except Exception as e:
        log.warning("rpc verify_owner failed: %s", e)

    await execute(
        "update dashboard_users set near_account_id=%s, near_public_key=%s, near_connected_at=now(), updated_at=now() where telegram_id=%s",
        (account_id, public_key, user_id),
    )
    # one‑time use nonce
    await execute("delete from dashboard_nonces where telegram_id=%s", (user_id,))

    return _json({"ok": True, "account_id": account_id}, request)


# ------------------------------ NEAR Token (NEP‑141) ------------------------------
async def _rpc_view_function(contract_id: str, method: str, args: Dict[str, Any]):
    args_b64 = base64.b64encode(json.dumps(args).encode()).decode()
    async with httpx.AsyncClient(timeout=10.0) as client:
        payload = {
            "jsonrpc": "2.0",
            "id": "view",
            "method": "query",
            "params": {
                "request_type": "call_function",
                "finality": "final",
                "account_id": contract_id,
                "method_name": method,
                "args_base64": args_b64,
            },
        }
        r = await client.post(NEAR_RPC_URL, json=payload)
        r.raise_for_status()
        res = r.json()["result"]["result"]
        return json.loads(bytes(res).decode())


async def near_token_summary(request: web.Request):
    await _auth_user(request)
    if not NEAR_TOKEN_CONTRACT:
        return _json({"enabled": False, "reason": "NEAR_TOKEN_CONTRACT not set"}, request)

    try:
        meta = await _rpc_view_function(NEAR_TOKEN_CONTRACT, "ft_metadata", {})
        total = await _rpc_view_function(NEAR_TOKEN_CONTRACT, "ft_total_supply", {})
    except Exception as e:
        return _json({"enabled": True, "error": f"rpc failed: {e}"}, request, status=502)

    # off‑chain events rollup
    roll = await fetchrow(
        """
        select
          coalesce(sum(case when kind='mint'  then amount when kind='reward' then amount else 0 end),0) as issued,
          coalesce(sum(case when kind='burn'  then amount when kind='fee'    then amount else 0 end),0) as burned_or_fees
        from dashboard_token_events
        """
    )

    return _json(
        {
            "enabled": True,
            "contract": NEAR_TOKEN_CONTRACT,
            "network": NEAR_NETWORK,
            "metadata": meta,
            "total_supply": total,
            "offchain": roll or {},
        },
        request,
    )

# ------------------------------ NEAR Account Overview ------------------------------
async def _rpc_view_account_near(account_id: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        payload = {
            "jsonrpc": "2.0","id":"view_account","method":"query",
            "params":{"request_type":"view_account","finality":"final","account_id":account_id}
        }
        r = await client.post(NEAR_RPC_URL, json=payload)
        r.raise_for_status()
        return r.json()["result"]

def yocto_to_near_str(yocto: str) -> str:
    try: return str(Decimal(yocto) / Decimal(10**24))
    except Exception: return "0"

async def near_account_overview(request: web.Request):
    await _auth_user(request)
    account_id = request.query.get("account_id")
    if not account_id:
        raise web.HTTPBadRequest(text="account_id required")
    tokens_csv = request.query.get("tokens","").strip()
    tokens = [t for t in tokens_csv.split(",") if t]
    acct = await _rpc_view_account_near(account_id)
    out = {
        "account_id": account_id,
        "near": {
            "amount_yocto": acct.get("amount","0"),
            "amount_near":  yocto_to_near_str(acct.get("amount","0")),
            "locked_yocto": acct.get("locked","0"),
            "locked_near":  yocto_to_near_str(acct.get("locked","0")),
            "storage_usage": acct.get("storage_usage",0),
            "code_hash": acct.get("code_hash")
        },
        "tokens": {}
    }
    for c in tokens:
        try:
            bal = await _rpc_view_function(c, "ft_balance_of", {"account_id": account_id})
        except Exception as e:
            bal = {"error": str(e)}
        out["tokens"][c] = bal
    return _json(out, request)

# ---------- NEAR: Wallet verbinden & Zahlungen ----------
async def set_near_account(request: web.Request):
    uid = await _auth_user(request)
    body = await request.json()
    acc  = (body.get("account_id") or "").strip()
    if not acc:
        raise web.HTTPBadRequest(text="account_id required")
    await execute("update dashboard_users set near_account_id=%s, near_connected_at=now(), updated_at=now() where telegram_id=%s",
                  (acc, uid))
    return _json({"ok": True, "near_account_id": acc}, request)

async def near_payments(request: web.Request):
    await _auth_user(request)
    account_id = request.query.get("account_id")
    limit = int(request.query.get("limit", "20"))
    if not account_id:
        raise web.HTTPBadRequest(text="account_id required")
    url = f"{NEARBLOCKS_API}/v1/account/{account_id}/activity?limit={limit}&order=desc"
    async with httpx.AsyncClient(timeout=10.0) as cx:
        r = await cx.get(url, headers={"accept":"application/json"})
        r.raise_for_status()
        j = r.json()
    # Filter: nur eingehende Native-NEAR Transfers
    items = []
    for it in j.get("activity", []):
        if it.get("type") == "TRANSFER" and it.get("receiver") == account_id:
            items.append({
              "ts": it.get("block_timestamp"),
              "tx_hash": it.get("tx_hash"),
              "from": it.get("signer"),
              "to": it.get("receiver"),
              "amount_yocto": it.get("delta_amount") or it.get("amount") or "0",
              "amount_near": _yocto_to_near(it.get("delta_amount") or it.get("amount") or "0"),
            })
    return _json({"account_id": account_id, "incoming": items[:limit]}, request)


# ------------------------------ TON Wallet ------------------------------
async def set_ton_address(request: web.Request):
    user_id = await _auth_user(request)
    body = await request.json()
    address = (body.get("address") or "").strip()
    await execute("update dashboard_users set ton_address=%s, updated_at=now() where telegram_id=%s",
                    (address, user_id))
    return _json({"ok": True, "ton_address": address}, request)

async def ton_payments(request: web.Request):
    await _auth_user(request)
    address = request.query.get("address", "").strip()
    limit   = int(request.query.get("limit", "20"))
    if not address:
        raise web.HTTPBadRequest(text="address required")
    headers={}
    if TON_API_KEY: headers["Authorization"] = f"Bearer {TON_API_KEY}"
    url = f"{TON_API_BASE}/v2/accounts/{address}/events?limit={limit}&subject_only=true"
    async with httpx.AsyncClient(timeout=10.0) as cx:
        r = await cx.get(url, headers=headers)
        r.raise_for_status()
        j = r.json()
    items=[]
    for ev in j.get("events", []):
        for act in ev.get("actions", []):
            if act.get("type")=="TonTransfer" and act.get("direction")=="in":
                items.append({
                  "ts": ev.get("timestamp"),
                  "tx_hash": ev.get("event_id"),
                  "from": act.get("from"),
                  "to": act.get("to"),
                  "amount_ton": act.get("amount"),  # nanotons/tons je nach API – direkt anzeigen
                })
    return _json({"address": address, "incoming": items[:limit]}, request)

async def wallets_overview(request: web.Request):
    user_id = await _auth_user(request)
    me = await fetchrow("select near_account_id, ton_address from dashboard_users where telegram_id=%s", (user_id,))
    watches = await fetch("select id, chain, account_id, label, meta, created_at from dashboard_watch_accounts order by id asc")
    return _json({"me": me, "watch": watches}, request)
# ------------------------------ Bot Mesh ------------------------------
async def mesh_health(request: web.Request):
    await _auth_user(request)
    rows = await fetch("select bot_slug, base_url, health_path, api_key from dashboard_bot_endpoints where is_active=true order by bot_slug")
    out = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for r in rows:
            url = r["base_url"].rstrip("/") + r["health_path"]
            headers = {"x-api-key": r["api_key"]} if r["api_key"] else {}
            try:
                resp = await client.get(url, headers=headers)
                out[r["bot_slug"]] = {"status": resp.status_code, "body": resp.json() if resp.headers.get("content-type","" ).startswith("application/json") else await resp.aread()[:200].decode(errors='ignore')}
                await execute("update dashboard_bot_endpoints set last_seen=now() where bot_slug=%s and base_url=%s", (r["bot_slug"], r["base_url"]))
            except Exception as e:
                out[r["bot_slug"]] = {"error": str(e)}
    return _json(out, request)


async def mesh_metrics(request: web.Request):
    await _auth_user(request)
    rows = await fetch("select bot_slug, base_url, metrics_path, api_key from dashboard_bot_endpoints where is_active=true order by bot_slug")
    out = {}
    async with httpx.AsyncClient(timeout=8.0) as client:
        for r in rows:
            url = r["base_url"].rstrip("/") + r["metrics_path"]
            headers = {"x-api-key": r["api_key"]} if r["api_key"] else {}
            try:
                resp = await client.get(url, headers=headers)
                out[r["bot_slug"]] = resp.json()
            except Exception as e:
                out[r["bot_slug"]] = {"error": str(e)}
    return _json(out, request)


# ------------------------------ route wiring ------------------------------
async def options_root(request: web.Request):
    return options_handler(request)


def register_devdash_routes(app: web.Application):
    # Doppelte Registrierung verhindern (Heroku Reloads, mehrfacher Aufruf)
    if app.get("_devdash_routes_registered"):
        return
    app["_devdash_routes_registered"] = True

    # WICHTIG: add_route("GET", ...) statt add_get(), damit kein automatisches HEAD registriert wird
    app.router.add_route("GET",  "/devdash/healthz",              healthz)
    app.router.add_post(        "/devdash/dev-login",             dev_login)
    app.router.add_post(        "/devdash/auth/telegram",         auth_telegram)
    app.router.add_route("GET", "/devdash/me",                    me)
    app.router.add_route("GET", "/devdash/metrics/overview",      overview)
    app.router.add_route("GET", "/devdash/bots",                  bots_list)
    app.router.add_post(        "/devdash/bots",                  bots_add)
    app.router.add_post(        "/devdash/bots/refresh",          bots_refresh)
    app.router.add_route("GET", "/devdash/near/account/overview", near_account_overview)
    app.router.add_post(        "/devdash/wallets/near",          set_near_account)
    app.router.add_route("GET", "/devdash/near/payments",         near_payments)
    app.router.add_post(        "/devdash/wallets/ton",           set_ton_address)
    app.router.add_route("GET", "/devdash/ton/payments",          ton_payments)
    app.router.add_route("GET", "/devdash/wallets",               wallets_overview)
    app.router.add_route("OPTIONS", "/devdash/{tail:.*}", options_handler)
    app.router.add_route("GET", "/devdash/mesh/health",         mesh_health)
    app.router.add_route("GET", "/devdash/mesh/metrics",        mesh_metrics)
    
    
# If you run this module standalone, boot a tiny aiohttp app for local testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = web.Application(middlewares=[cors_middleware])
    register_devdash_routes(app)
    app.on_startup.append(lambda app: ensure_tables())
    web.run_app(app, port=int(os.getenv("PORT", 8080)))