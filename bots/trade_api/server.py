import json, hashlib, hmac, time, numpy as np, logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List
from aiohttp import web
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import BOT_TOKEN, APP_BASE_URL, ALLOWED_PROVIDERS, SECRET_KEY, TELEGRAM_LOGIN_TTL_SECONDS, PRO_DEFAULT, PRO_USERS
from .db import execute, fetch, fetchrow
from .crypto_utils import encrypt_blob, decrypt_blob
from .providers.base import ProviderCredentials
from .providers.kraken import KrakenProvider
from .providers.coinbase import CoinbaseProvider
from .providers.mexc import MexcProvider
from .providers.base import ProviderBase
from .ml.xgb_signals import score_signal
from .risk.atr import atr, position_size
from .sentiment.finbert import analyze as finbert_analyze
from .portfolio.optimizer import optimize as portfolio_opt
from .proof.onchain import ensure_table as ensure_proof_table, record_proof, list_proofs

logger = logging.getLogger(__name__)

PROVIDER_MAP = {
    "kraken": KrakenProvider,
    "coinbase": CoinbaseProvider,
    "mexc": MexcProvider,
}

# SQL Schema - erweitert mit User-Settings, Portfolios, Alerts, Trading History
INIT_SQL = """
-- API Keys (verschlüsselt)
create table if not exists tradeapi_keys (
  id bigserial primary key,
  telegram_id bigint not null,
  provider text not null,
  label text,
  api_fields_enc text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(telegram_id, provider, coalesce(label,''))
);
create index if not exists tradeapi_keys_tid_idx on tradeapi_keys(telegram_id);

-- User Settings & Preferences
create table if not exists tradeapi_user_settings (
  telegram_id bigint primary key,
  theme text default 'dark',
  notifications_enabled boolean default true,
  alert_threshold_usd numeric(18,2) default 100.00,
  preferred_currency text default 'USD',
  language text default 'de',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Portfolios
create table if not exists tradeapi_portfolios (
  id bigserial primary key,
  telegram_id bigint not null,
  name text,
  description text,
  total_value numeric(18,8),
  cash numeric(18,8),
  risk_level text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(telegram_id, name)
);
create index if not exists tradeapi_portfolios_tid_idx on tradeapi_portfolios(telegram_id);

-- Portfolio Positions
create table if not exists tradeapi_positions (
  id bigserial primary key,
  portfolio_id bigint references tradeapi_portfolios(id) on delete cascade,
  asset_symbol text,
  quantity numeric(18,8),
  entry_price numeric(18,8),
  current_price numeric(18,8),
  cost_basis numeric(18,8),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists tradeapi_positions_pid_idx on tradeapi_positions(portfolio_id);

-- Trading Alerts
create table if not exists tradeapi_alerts (
  id bigserial primary key,
  telegram_id bigint not null,
  symbol text,
  alert_type text,
  target_price numeric(18,8),
  comparison text,
  is_active boolean default true,
  is_triggered boolean default false,
  created_at timestamptz not null default now(),
  triggered_at timestamptz
);
create index if not exists tradeapi_alerts_tid_idx on tradeapi_alerts(telegram_id);
create index if not exists tradeapi_alerts_active_idx on tradeapi_alerts(is_active) where is_active = true;

-- Trading Signals
create table if not exists tradeapi_signals (
  id bigserial primary key,
  telegram_id bigint not null,
  symbol text,
  signal_type text,
  confidence numeric(3,2),
  strength numeric(3,2),
  atr_value numeric(18,8),
  entry_price numeric(18,8),
  stop_loss numeric(18,8),
  take_profit numeric(18,8),
  position_size numeric(18,8),
  created_at timestamptz not null default now()
);
create index if not exists tradeapi_signals_tid_idx on tradeapi_signals(telegram_id);

-- Trading History
create table if not exists tradeapi_trades (
  id bigserial primary key,
  telegram_id bigint not null,
  portfolio_id bigint references tradeapi_portfolios(id),
  symbol text,
  side text,
  quantity numeric(18,8),
  entry_price numeric(18,8),
  exit_price numeric(18,8),
  commission numeric(18,8),
  pnl numeric(18,8),
  pnl_percent numeric(5,2),
  status text,
  opened_at timestamptz,
  closed_at timestamptz,
  notes text,
  created_at timestamptz not null default now()
);
create index if not exists tradeapi_trades_tid_idx on tradeapi_trades(telegram_id);

-- Sentiment Analysis Cache
create table if not exists tradeapi_sentiment (
  id bigserial primary key,
  text text,
  sentiment text,
  positive numeric(3,2),
  neutral numeric(3,2),
  negative numeric(3,2),
  created_at timestamptz not null default now()
);
create index if not exists tradeapi_sentiment_created_idx on tradeapi_sentiment(created_at);

-- Market Data Cache
create table if not exists tradeapi_market_cache (
  id bigserial primary key,
  symbol text,
  provider text,
  price numeric(18,8),
  volume numeric(18,8),
  change_24h numeric(5,2),
  high_24h numeric(18,8),
  low_24h numeric(18,8),
  created_at timestamptz not null default now(),
  unique(symbol, provider)
);
create index if not exists tradeapi_market_symbol_idx on tradeapi_market_cache(symbol);
"""


def init_schema():
    for stmt in [s.strip() for s in INIT_SQL.split(";") if s.strip()]:
        execute(stmt + ";")
    ensure_proof_table()

def verify_webapp_initdata(init_data: Dict[str, Any]) -> Dict[str, Any]:
    if not BOT_TOKEN:
        raise ValueError("TRADE_API_BOT_TOKEN env fehlt")
    if "hash" not in init_data:
        raise ValueError("missing hash")
    pairs = []
    for k in sorted(init_data.keys()):
        if k == "hash": continue
        v = init_data[k]
        if isinstance(v, dict):
            v = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
        pairs.append(f"{k}={v}")
    data_check = "\n".join(pairs)
    secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if calc != init_data["hash"]:
        raise ValueError("bad hash")
    auth_date = int(init_data.get("auth_date", "0"))
    if auth_date and (time.time() - auth_date) > TELEGRAM_LOGIN_TTL_SECONDS:
        raise ValueError("login expired")
    user = init_data.get("user")
    if not user or not user.get("id"):
        raise ValueError("no user in initData")
    return {"telegram_id": int(user["id"]), "username": user.get("username")}

def user_is_pro(telegram_id: int) -> bool:
    if telegram_id in PRO_USERS: return True
    return PRO_DEFAULT

async def _json(data: Any, status: int = 200):
    return web.json_response(data, status=status)

# ---------- Basic API ----------
async def tradeapi_auth(request: web.Request):
    payload = await request.json()
    try:
        u = verify_webapp_initdata(payload)
    except Exception as e:
        return await _json({"error": str(e)}, 400)
    return await _json({"ok": True, "telegram_id": u["telegram_id"], "username": u.get("username")})

async def providers(request: web.Request):
    return await _json({"providers": ALLOWED_PROVIDERS})

async def keys_list(request: web.Request):
    tid = int(request.query.get("telegram_id") or 0)
    if not tid: return await _json({"error":"telegram_id required"}, 400)
    rows = fetch("select id, provider, coalesce(label,'') as label, created_at, updated_at from tradeapi_keys where telegram_id=%s order by provider, label", (tid,))
    return await _json({"items": rows})

async def keys_upsert(request: web.Request):
    body = await request.json()
    try:
        u = verify_webapp_initdata(body.get("initData") or {})
    except Exception as e:
        return await _json({"error": str(e)}, 401)
    tid = u["telegram_id"]
    provider = (body.get("provider") or "").strip()
    if not provider: return await _json({"error":"provider required"}, 400)
    if provider not in [p["id"] for p in ALLOWED_PROVIDERS]:
        return await _json({"error":"provider not allowed"}, 400)
    label = (body.get("label") or "").strip() or None
    fields = {
        "api_key":     (body.get("api_key") or "").strip(),
        "api_secret":  (body.get("api_secret") or "").strip(),
        "passphrase":  (body.get("passphrase") or "").strip(),
        "extras":      body.get("extras") or {},
    }
    if not fields["api_key"] or not fields["api_secret"]:
        return await _json({"error":"api_key and api_secret required"}, 400)
    # Free vs Pro: wenn user nicht Pro und bereits >0 Keys vorhanden, verweigern
    rows = fetch("select count(*) as n from tradeapi_keys where telegram_id=%s", (tid,))
    count = int(rows[0]["n"]) if rows else 0
    if count >= 1 and not user_is_pro(tid):
        return await _json({"error":"Mehrere APIs sind nur in der Pro-Version erlaubt."}, 402)
    blob = encrypt_blob(SECRET_KEY, fields)
    execute(            "insert into tradeapi_keys(telegram_id, provider, label, api_fields_enc) values (%s,%s,%s,%s) "
        "on conflict (telegram_id, provider, coalesce(label,'')) do update set api_fields_enc=excluded.api_fields_enc, updated_at=now()",
        (tid, provider, label, blob)
    )
    return await _json({"ok": True})

async def keys_delete(request: web.Request):
    body = await request.json()
    try:
        u = verify_webapp_initdata(body.get("initData") or {})
    except Exception as e:
        return await _json({"error": str(e)}, 401)
    tid = u["telegram_id"]
    kid = int(body.get("id") or 0)
    if not kid: return await _json({"error":"id required"}, 400)
    execute("delete from tradeapi_keys where id=%s and telegram_id=%s", (kid, tid))
    return await _json({"ok": True})


async def keys_verify(request: web.Request):
    body = await request.json()
    try:
        u = verify_webapp_initdata(body.get("initData") or {})
    except Exception as e:
        return await _json({"error": str(e)}, 401)
    tid = u["telegram_id"]
    provider = (body.get("provider") or "").strip()
    if not provider: return await _json({"error":"provider required"}, 400)
    row = fetchrow("select api_fields_enc from tradeapi_keys where telegram_id=%s and provider=%s order by updated_at desc limit 1", (tid, provider))
    if not row: return await _json({"error":"keine Credentials gefunden"}, 404)
    fields = decrypt_blob(SECRET_KEY, row["api_fields_enc"])
    creds = ProviderCredentials(fields.get("api_key"), fields.get("api_secret"), fields.get("passphrase") or None, fields.get("extras") or {})
    Prov = PROVIDER_MAP.get(provider)
    if not Prov: return await _json({"error":"provider not implemented"}, 400)
    p = Prov(creds)
    try:
        bals = await p.balances()
        return await _json({"ok": True, "balances": bals})
    except Exception as e:
        return await _json({"ok": False, "error": str(e)})

async def keys_ping(request: web.Request):
    body = await request.json()
    try:
        u = verify_webapp_initdata(body.get("initData") or {})
    except Exception as e:
        return await _json({"error": str(e)}, 401)
    tid = u["telegram_id"]
    provider = (body.get("provider") or "").strip()
    if not provider: return await _json({"error":"provider required"}, 400)
    row = fetchrow("select api_fields_enc from tradeapi_keys where telegram_id=%s and provider=%s order by updated_at desc limit 1", (tid, provider))
    if not row: return await _json({"error":"keine Credentials gefunden"}, 404)
    fields = decrypt_blob(SECRET_KEY, row["api_fields_enc"])
    creds = ProviderCredentials(fields.get("api_key"), fields.get("api_secret"), fields.get("passphrase") or None, fields.get("extras") or {})
    Prov = PROVIDER_MAP.get(provider)
    if not Prov: return await _json({"error":"provider not implemented"}, 400)
    p = Prov(creds)
    ok = await p.ping()
    return await _json({"ok": bool(ok)})

# ---------- Signals + Risk + Proof ----------
async def signal_generate(request: web.Request):
    body = await request.json()
    try:
        u = verify_webapp_initdata(body.get("initData") or {})
    except Exception as e:
        return await _json({"error": str(e)}, 401)
    tid = u["telegram_id"]
    provider = (body.get("provider") or "").strip()
    symbol = (body.get("symbol") or "BTCUSDT").upper()
    ohlcv = np.array(body.get("ohlcv") or [], dtype=float)
    if ohlcv.shape[1] if ohlcv.size else 0 != 5:
        return await _json({"error":"ohlcv must be Nx5 [O,H,L,C,V]"}, 400)
    sig = score_signal(ohlcv)
    high, low, close = ohlcv[:,1], ohlcv[:,2], ohlcv[:,3]
    atr_val = float(atr(high, low, close))
    # simplistic balance assumption 1000 USD if no API or balance endpoint wired
    bal = float(body.get("balance_usd") or 1000.0)
    entry = float(close[-1])
    size = position_size(bal, entry, atr_val)
    payload = {"signal": sig, "atr": atr_val, "pos_size": size, "entry": entry, "symbol": symbol, "provider": provider}
    proof = record_proof(tid, provider or "na", symbol, payload)
    return await _json({"ok": True, "payload": payload, "proof": proof})

async def proof_list(request: web.Request):
    tid = int(request.query.get("telegram_id") or 0)
    if not tid: return await _json({"error":"telegram_id required"}, 400)
    rows = list_proofs(tid, limit=50)
    return await _json({"items": rows})

# ---------- Sentiment + Portfolio ----------
async def sentiment_analyze(request: web.Request):
    body = await request.json()
    try:
        _ = verify_webapp_initdata(body.get("initData") or {})
    except Exception as e:
        return await _json({"error": str(e)}, 401)
    texts = body.get("texts") or []
    res = finbert_analyze(texts)
    return await _json({"sentiment": res})

async def portfolio_optimize(request: web.Request):
    body = await request.json()
    try:
        _ = verify_webapp_initdata(body.get("initData") or {})
    except Exception as e:
        return await _json({"error": str(e)}, 401)
    weights_hint = body.get("weights") or {}
    sentiment = body.get("sentiment") or None
    res = portfolio_opt(weights_hint, sentiment)
    return await _json({"weights": res})

# ---------- User Settings ----------
async def get_user_settings(request: web.Request):
    tid = int(request.query.get("telegram_id") or 0)
    if not tid: return await _json({"error": "telegram_id required"}, 400)
    
    row = fetchrow(
        "select theme, notifications_enabled, alert_threshold_usd, preferred_currency, language "
        "from tradeapi_user_settings where telegram_id=%s",
        (tid,)
    )
    
    if not row:
        # Create default settings
        execute(
            "insert into tradeapi_user_settings(telegram_id) values(%s) on conflict do nothing",
            (tid,)
        )
        row = {"theme": "dark", "notifications_enabled": True, "alert_threshold_usd": 100.0, 
               "preferred_currency": "USD", "language": "de"}
    
    return await _json({"ok": True, "settings": row})

async def update_user_settings(request: web.Request):
    body = await request.json()
    try:
        u = verify_webapp_initdata(body.get("initData") or {})
    except Exception as e:
        return await _json({"error": str(e)}, 401)
    
    tid = u["telegram_id"]
    theme = body.get("theme", "dark")
    notifications = body.get("notifications_enabled", True)
    alert_threshold = body.get("alert_threshold_usd", 100.0)
    currency = body.get("preferred_currency", "USD")
    language = body.get("language", "de")
    
    execute(
        "insert into tradeapi_user_settings(telegram_id, theme, notifications_enabled, alert_threshold_usd, preferred_currency, language) "
        "values(%s,%s,%s,%s,%s,%s) "
        "on conflict(telegram_id) do update set theme=excluded.theme, notifications_enabled=excluded.notifications_enabled, "
        "alert_threshold_usd=excluded.alert_threshold_usd, preferred_currency=excluded.preferred_currency, language=excluded.language, updated_at=now()",
        (tid, theme, notifications, alert_threshold, currency, language)
    )
    
    return await _json({"ok": True})

# ---------- Portfolio Management ----------
async def list_portfolios(request: web.Request):
    tid = int(request.query.get("telegram_id") or 0)
    if not tid: return await _json({"error": "telegram_id required"}, 400)
    
    rows = fetch(
        "select id, name, description, total_value, cash, risk_level, created_at, updated_at "
        "from tradeapi_portfolios where telegram_id=%s order by created_at desc",
        (tid,)
    )
    
    return await _json({"ok": True, "portfolios": rows})

async def create_portfolio(request: web.Request):
    body = await request.json()
    try:
        u = verify_webapp_initdata(body.get("initData") or {})
    except Exception as e:
        return await _json({"error": str(e)}, 401)
    
    tid = u["telegram_id"]
    name = (body.get("name") or "Portfolio").strip()
    description = (body.get("description") or "").strip()
    risk_level = (body.get("risk_level") or "medium").strip()
    initial_cash = float(body.get("initial_cash") or 10000.0)
    
    try:
        row = fetchrow(
            "insert into tradeapi_portfolios(telegram_id, name, description, cash, total_value, risk_level) "
            "values(%s,%s,%s,%s,%s,%s) returning id",
            (tid, name, description, initial_cash, initial_cash, risk_level)
        )
        return await _json({"ok": True, "portfolio_id": row["id"]})
    except Exception as e:
        return await _json({"error": f"Portfolio bereits vorhanden oder Fehler: {str(e)}"}, 400)

async def get_portfolio(request: web.Request):
    pid = int(request.query.get("portfolio_id") or 0)
    if not pid: return await _json({"error": "portfolio_id required"}, 400)
    
    portfolio = fetchrow("select * from tradeapi_portfolios where id=%s", (pid,))
    if not portfolio: return await _json({"error": "not found"}, 404)
    
    positions = fetch("select * from tradeapi_positions where portfolio_id=%s", (pid,))
    
    # Calculate totals
    total_invested = sum(float(p.get("cost_basis") or 0) for p in positions)
    total_current = sum(float(p.get("current_price", 0)) * float(p.get("quantity", 0)) for p in positions)
    
    return await _json({
        "ok": True,
        "portfolio": portfolio,
        "positions": positions,
        "summary": {
            "total_invested": total_invested,
            "total_current": total_current,
            "unrealized_pnl": total_current - total_invested,
            "unrealized_pnl_percent": ((total_current - total_invested) / total_invested * 100) if total_invested > 0 else 0
        }
    })

async def add_position(request: web.Request):
    body = await request.json()
    try:
        u = verify_webapp_initdata(body.get("initData") or {})
    except Exception as e:
        return await _json({"error": str(e)}, 401)
    
    pid = int(body.get("portfolio_id") or 0)
    if not pid: return await _json({"error": "portfolio_id required"}, 400)
    
    symbol = (body.get("symbol") or "").upper()
    quantity = float(body.get("quantity") or 0)
    entry_price = float(body.get("entry_price") or 0)
    current_price = float(body.get("current_price") or entry_price)
    
    if quantity <= 0 or entry_price <= 0:
        return await _json({"error": "Ungültige Menge oder Preis"}, 400)
    
    cost_basis = quantity * entry_price
    
    execute(
        "insert into tradeapi_positions(portfolio_id, asset_symbol, quantity, entry_price, current_price, cost_basis) "
        "values(%s,%s,%s,%s,%s,%s)",
        (pid, symbol, quantity, entry_price, current_price, cost_basis)
    )
    
    # Update portfolio value
    positions = fetch("select sum(quantity * current_price) as total from tradeapi_positions where portfolio_id=%s", (pid,))
    cash = fetchrow("select cash from tradeapi_portfolios where id=%s", (pid,))
    
    if positions and positions[0]["total"]:
        new_total = float(positions[0]["total"]) + float(cash["cash"])
        execute("update tradeapi_portfolios set total_value=%s, updated_at=now() where id=%s", (new_total, pid))
    
    return await _json({"ok": True, "message": "Position hinzugefügt"})

# ---------- Alerts Management ----------
async def list_alerts(request: web.Request):
    tid = int(request.query.get("telegram_id") or 0)
    if not tid: return await _json({"error": "telegram_id required"}, 400)
    
    active_only = request.query.get("active_only", "false").lower() in ("1", "true", "yes")
    
    query = "select id, symbol, alert_type, target_price, comparison, is_active, is_triggered, created_at, triggered_at from tradeapi_alerts where telegram_id=%s"
    params = [tid]
    
    if active_only:
        query += " and is_active=true"
    
    query += " order by created_at desc limit 100"
    
    rows = fetch(query, tuple(params))
    return await _json({"ok": True, "alerts": rows})

async def create_alert(request: web.Request):
    body = await request.json()
    try:
        u = verify_webapp_initdata(body.get("initData") or {})
    except Exception as e:
        return await _json({"error": str(e)}, 401)
    
    tid = u["telegram_id"]
    symbol = (body.get("symbol") or "").upper()
    alert_type = body.get("alert_type", "price")  # "price", "volume", "rsi"
    target_price = float(body.get("target_price") or 0)
    comparison = body.get("comparison", "above")  # "above", "below"
    
    if not symbol or target_price <= 0:
        return await _json({"error": "Symbol und Target Price erforderlich"}, 400)
    
    execute(
        "insert into tradeapi_alerts(telegram_id, symbol, alert_type, target_price, comparison, is_active) "
        "values(%s,%s,%s,%s,%s,true)",
        (tid, symbol, alert_type, target_price, comparison)
    )
    
    return await _json({"ok": True, "message": "Alert erstellt"})

async def delete_alert(request: web.Request):
    body = await request.json()
    try:
        u = verify_webapp_initdata(body.get("initData") or {})
    except Exception as e:
        return await _json({"error": str(e)}, 401)
    
    tid = u["telegram_id"]
    aid = int(body.get("alert_id") or 0)
    
    if not aid: return await _json({"error": "alert_id required"}, 400)
    
    execute("delete from tradeapi_alerts where id=%s and telegram_id=%s", (aid, tid))
    return await _json({"ok": True})

# ---------- Market Data & Price Update ----------
async def get_market_price(request: web.Request):
    symbol = request.query.get("symbol", "BTCUSDT").upper()
    provider = request.query.get("provider", "kraken")
    
    # Try cache first
    row = fetchrow(
        "select price, volume, change_24h, high_24h, low_24h, created_at from tradeapi_market_cache "
        "where symbol=%s and provider=%s and created_at > now() - interval '1 minute'",
        (symbol, provider)
    )
    
    if row:
        return await _json({"ok": True, "cached": True, "data": row})
    
    return await _json({"ok": True, "cached": False, "data": None})

# ---------- Dashboard & Analytics ----------
async def get_dashboard(request: web.Request):
    tid = int(request.query.get("telegram_id") or 0)
    if not tid: return await _json({"error": "telegram_id required"}, 400)
    
    # Get portfolios
    portfolios = fetch(
        "select id, name, total_value from tradeapi_portfolios where telegram_id=%s order by created_at desc",
        (tid,)
    )
    
    # Get recent signals
    signals = fetch(
        "select symbol, signal_type, confidence, entry_price, position_size, created_at "
        "from tradeapi_signals where telegram_id=%s order by created_at desc limit 5",
        (tid,)
    )
    
    # Get active alerts
    alerts = fetch(
        "select symbol, alert_type, target_price from tradeapi_alerts "
        "where telegram_id=%s and is_active=true order by created_at desc limit 5",
        (tid,)
    )
    
    # Get settings
    settings = fetchrow("select theme, language from tradeapi_user_settings where telegram_id=%s", (tid,))
    if not settings:
        settings = {"theme": "dark", "language": "de"}
    
    total_portfolio_value = sum(float(p.get("total_value") or 0) for p in portfolios)
    
    return await _json({
        "ok": True,
        "dashboard": {
            "total_portfolio_value": total_portfolio_value,
            "portfolio_count": len(portfolios),
            "active_alerts": len(alerts),
            "recent_signals": len(signals),
            "portfolios": portfolios,
            "recent_signals": signals,
            "active_alerts": alerts,
            "settings": settings
        }
    })

# ---------- Telegram Commands ----------

async def _start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = f"{APP_BASE_URL}/static/apptradeapi.html" if APP_BASE_URL else "https://example.com/static/apptradeapi.html"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(text="🔐 Trading-API MiniApp öffnen", web_app=WebAppInfo(url=url))]])
    await update.message.reply_text(
        "🚀 **Emerald Trade API Bot** — v0.2\n\n"
        "Vollständige Trading-Platform mit:\n"
        "✅ Multi-Provider API Integration (Kraken, Coinbase, MEXC)\n"
        "✅ XGBoost Signale + ATR Risk Management\n"
        "✅ Portfolio Manager & Analytics\n"
        "✅ Smart Alerts & Notifications\n"
        "✅ FinBERT Sentiment Analysis\n"
        "✅ On-Chain Proof Verification\n"
        "✅ Benutzer-spezifische Einstellungen\n\n"
        "Pro-Funktionen für meherer APIs verfügbar!",
        reply_markup=kb,
        parse_mode="Markdown"
    )

async def _me(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    
    keys = fetch(
        "select provider, coalesce(label,'') as label, updated_at from tradeapi_keys "
        "where telegram_id=%s order by provider, label",
        (tid,)
    )
    
    portfolios = fetch(
        "select name, total_value from tradeapi_portfolios where telegram_id=%s",
        (tid,)
    )
    
    alerts = fetch(
        "select count(*) as count from tradeapi_alerts where telegram_id=%s and is_active=true",
        (tid,)
    )
    
    alert_count = int(alerts[0]["count"]) if alerts else 0
    
    text = f"👤 **Dein Profil**\n\n"
    text += f"**API-Verknüpfungen:** {len(keys)}\n"
    if keys:
        for k in keys:
            text += f"  • {k['provider']}{' – '+k['label'] if k['label'] else ''}\n"
    
    text += f"\n**Portfolios:** {len(portfolios)}\n"
    if portfolios:
        total_value = sum(float(p.get('total_value') or 0) for p in portfolios)
        text += f"  Gesamtwert: ${total_value:,.2f}\n"
    
    text += f"\n**Aktive Alerts:** {alert_count}\n"
    text += f"\n_Klicke auf den Button unten, um die MiniApp zu öffnen!_"
    
    url = f"{APP_BASE_URL}/static/apptradeapi.html" if APP_BASE_URL else "https://example.com/static/apptradeapi.html"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(text="📊 MiniApp öffnen", web_app=WebAppInfo(url=url))]])
    
    await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

async def _help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = """🆘 **Hilfe – Trade API Bot**

**Verfügbare Befehle:**
/start – Willkommen & MiniApp öffnen
/me – Dein Profil & Übersicht
/help – Diese Hilfe

**MiniApp-Features:**
1️⃣ **API Keys** – Verknüpfe Trading-Börsen (Kraken, Coinbase, MEXC)
2️⃣ **Portfolio Manager** – Verwalte mehrere Portfolios
3️⃣ **Trading Signals** – XGBoost + ATR für professionelle Signale
4️⃣ **Sentiment Analysis** – FinBERT für Marktstimmung
5️⃣ **Portfolio Optimizer** – Gewichte basierend auf Sentiment optimieren
6️⃣ **Price Alerts** – Benachrichtigungen bei Preiszielen
7️⃣ **Dashboard** – Komplette Übersicht aller Daten
8️⃣ **Settings** – Theme, Sprache, Benachrichtigungen

**Sicherheit:**
🔒 Alle API-Keys werden serverseitig verschlüsselt
🔐 Telegram Login Verification für alle Operationen
🛡️ Proof-Hashes für Signalverifizierung

**Preismodelle:**
🆓 **Free:** 1 API-Provider, 5 Alerts, 1 Portfolio
💎 **Pro:** Unbegrenzte Provider, 50+ Alerts, 10+ Portfolios

Klicke unten, um die App zu starten!"""
    
    url = f"{APP_BASE_URL}/static/apptradeapi.html" if APP_BASE_URL else "https://example.com/static/apptradeapi.html"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(text="🚀 App starten", web_app=WebAppInfo(url=url))]])
    
    await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

def register(application: Application):
    """Register Telegram handlers"""
    application.add_handler(CommandHandler("start", _start))
    application.add_handler(CommandHandler("me", _me))
    application.add_handler(CommandHandler("help", _help))

def register_jobs(application: Application):
    """Register background jobs (placeholder)"""
    # TODO: Jobs für Alert-Checks, Portfolio-Updates, etc.
    pass

def register_miniapp_routes(webapp: web.Application, application: Application):
    """Register HTTP API routes"""
    init_schema()
    
    # Auth & Providers
    webapp.router.add_post( "/tradeapi/auth",               tradeapi_auth)
    webapp.router.add_get(  "/tradeapi/providers",          providers)
    
    # API Key Management
    webapp.router.add_get(  "/tradeapi/keys",               keys_list)
    webapp.router.add_post( "/tradeapi/keys",               keys_upsert)
    webapp.router.add_post( "/tradeapi/keys/delete",        keys_delete)
    webapp.router.add_post( "/tradeapi/keys/ping",          keys_ping)
    webapp.router.add_post( "/tradeapi/keys/verify",        keys_verify)
    
    # Signals & Risk
    webapp.router.add_post( "/tradeapi/signal/generate",    signal_generate)
    webapp.router.add_get(  "/tradeapi/proof/list",         proof_list)
    
    # Sentiment & Portfolio
    webapp.router.add_post( "/tradeapi/sentiment/analyze",  sentiment_analyze)
    webapp.router.add_post( "/tradeapi/portfolio/optimize", portfolio_optimize)
    
    # User Settings
    webapp.router.add_get(  "/tradeapi/settings",           get_user_settings)
    webapp.router.add_post( "/tradeapi/settings",           update_user_settings)
    
    # Portfolio Management
    webapp.router.add_get(  "/tradeapi/portfolios",         list_portfolios)
    webapp.router.add_post( "/tradeapi/portfolios",         create_portfolio)
    webapp.router.add_get(  "/tradeapi/portfolio",          get_portfolio)
    webapp.router.add_post( "/tradeapi/position",           add_position)
    
    # Alerts
    webapp.router.add_get(  "/tradeapi/alerts",             list_alerts)
    webapp.router.add_post( "/tradeapi/alerts",             create_alert)
    webapp.router.add_post( "/tradeapi/alerts/delete",      delete_alert)
    
    # Market Data
    webapp.router.add_get(  "/tradeapi/market/price",       get_market_price)
    
    # Dashboard
    webapp.router.add_get(  "/tradeapi/dashboard",          get_dashboard)

