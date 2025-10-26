
from aiohttp import web
import json, httpx, hmac, hashlib, time
import os
from models import (                                          # nutzt deine DB-API
    list_tenants_for_user, ensure_default_tenant_for_user,
    list_routes, create_route, update_route, delete_route,
    stats, list_connectors, upsert_connector
)

BOT_TOKEN = (
    os.environ.get("BOT2_TOKEN")                     # primär: dein Crossposter (wie von dir gewünscht)
    or os.environ.get("TELEGRAM_BOT_TOKEN_CROSSPOSTER")  # optionaler Alias, falls du ihn nutzt
)
if not BOT_TOKEN:
    raise RuntimeError("BOT2_TOKEN (Crossposter-Bot-Token) ist nicht gesetzt.")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def verify_init_data(init_data: str, bot_token: str) -> dict:
    """
    Telegram WebApp Login-Verify (Serverseite).
    Erwartet raw initData (querystring-ähnlich) aus Header X-Telegram-Init-Data.
    """
    if not init_data:
        raise web.HTTPUnauthorized(text="missing initData")
    # in key=value&... zerlegen
    pairs = {}
    for kv in init_data.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            pairs[k] = v
    if "hash" not in pairs:
        raise web.HTTPUnauthorized(text="missing hash")
    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs.keys()) if k != "hash")
    secret = hashlib.sha256(bot_token.encode()).digest()
    calc = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if calc != pairs["hash"]:
        raise web.HTTPUnauthorized(text="bad hash")
    if "auth_date" in pairs and time.time() - int(pairs["auth_date"]) > int(os.getenv("TELEGRAM_LOGIN_TTL_SECONDS", "86400")):
        raise web.HTTPUnauthorized(text="login expired")
    # minimal user-Objekt zusammensetzen
    user_json = pairs.get("user", "{}")
    try:
        user = json.loads(user_json)
    except Exception:
        user = {}
    if not user.get("id"):
        raise web.HTTPUnauthorized(text="no user in initData")
    return {"user": user}

async def _current_user(request: web.Request):
    init = request.headers.get("X-Telegram-Init-Data", "")
    return verify_init_data(init, BOT_TOKEN)

async def _tg_get(method: str, params: dict):
    async with httpx.AsyncClient(timeout=20) as cx:
        r = await cx.get(f"{TELEGRAM_API}/{method}", params=params)
        r.raise_for_status()
        j = r.json()
        if not j.get("ok"):
            raise web.HTTPBadRequest(text=json.dumps(j))
        return j["result"]

# ----- Handlers -----
async def health(_): return web.json_response({"ok": True})
# ---- (optional) statisches Ausliefern der Mini-App-HTML, falls keine CDN-URL gesetzt ist
_HTML_CACHE = None
def _load_html():
    global _HTML_CACHE
    if _HTML_CACHE is None:
        # relative Pfade okay, wenn appcrossposter.html im Projekt-Root liegt
        with open(os.path.join(os.path.dirname(__file__), "appcrossposter.html"), "r", encoding="utf-8") as f:
            _HTML_CACHE = f.read()
    return _HTML_CACHE

async def crossposter_page(request: web.Request):
    html = _load_html()
    return web.Response(text=html, content_type="text/html; charset=utf-8")

async def tenants(request: web.Request):
    user = await _current_user(request)
    uid = user["user"]["id"]
    rows = await list_tenants_for_user(uid)
    if not rows:
        await ensure_default_tenant_for_user(user["user"])
        rows = await list_tenants_for_user(uid)
    return web.json_response([dict(r) for r in rows])

async def routes_get(request: web.Request):
    user = await _current_user(request)
    uid = user["user"]["id"]
    tenant_id = int(request.query.get("tenant_id"))
    rows = await list_routes(tenant_id, uid)
    out = [dict(id=r["id"], tenant_id=tenant_id, source_chat_id=r["source_chat_id"],
                destinations=r["destinations"], transform=r["transform"],
                filters=r["filters"], active=r["active"]) for r in rows]
    return web.json_response(out)

async def routes_post(request: web.Request):
    user = await _current_user(request)
    uid = user["user"]["id"]
    p = await request.json()
    row = await create_route(p["tenant_id"], uid, p["source_chat_id"],
                             p["destinations"], p.get("transform", {}),
                             p.get("filters", {}), p.get("active", True))
    return web.json_response(dict(
        id=row["id"], tenant_id=row["tenant_id"], source_chat_id=row["source_chat_id"],
        destinations=row["destinations"], transform=row["transform"],
        filters=row["filters"], active=row["active"]
    ))

async def routes_patch(request: web.Request):
    user = await _current_user(request)
    uid = user["user"]["id"]
    rid = int(request.match_info["route_id"])
    p = await request.json()
    row = await update_route(rid, p["tenant_id"], uid, p["source_chat_id"],
                             p["destinations"], p.get("transform", {}),
                             p.get("filters", {}), p.get("active", True))
    if not row:
        raise web.HTTPNotFound(text="Route nicht gefunden")
    return web.json_response(dict(
        id=row["id"], tenant_id=row["tenant_id"], source_chat_id=row["source_chat_id"],
        destinations=row["destinations"], transform=row["transform"],
        filters=row["filters"], active=row["active"]
    ))

async def routes_delete(request: web.Request):
    user = await _current_user(request)
    uid = user["user"]["id"]
    rid = int(request.match_info["route_id"])
    tenant_id = int(request.query.get("tenant_id"))
    await delete_route(rid, tenant_id, uid)
    return web.json_response({"ok": True})

async def stats_get(request: web.Request):
    user = await _current_user(request)
    uid = user["user"]["id"]
    tenant_id = int(request.query.get("tenant_id"))
    total, by_status = await stats(tenant_id, uid)
    return web.json_response({"routes": total, "by_status": by_status})

async def connectors_get(request: web.Request):
    user = await _current_user(request)
    tenant_id = int(request.query.get("tenant_id"))
    rows = await list_connectors(tenant_id)
    return web.json_response([dict(r) for r in rows])

async def connectors_post(request: web.Request):
    user = await _current_user(request)
    p = await request.json()
    row = await upsert_connector(p["tenant_id"], p["type"], p.get("label","default"),
                                 p["config"], p.get("active", True))
    return web.json_response({"ok": True, "id": row["id"]})

# --- Gruppen-Tools: @username auflösen + Rechte prüfen ---
async def chat_resolve(request: web.Request):
    q = request.query.get("q","")
    if q.startswith("@"):
        res = await _tg_get("getChat", {"chat_id": q})
        return web.json_response({"chat_id": res["id"], "title": res.get("title")})
    return web.json_response({"chat_id": int(q)})

async def chat_check_admin(request: web.Request):
    user = await _current_user(request)
    chat_id = int(request.query.get("chat_id"))
    you = await _tg_get("getChatMember", {"chat_id": chat_id, "user_id": user["user"]["id"]})
    me  = await _tg_get("getMe", {})
    bot = await _tg_get("getChatMember", {"chat_id": chat_id, "user_id": me["id"]})
    can_post = bot.get("status") in ("creator","administrator","member")
    return web.json_response({"you_status": you.get("status"), "bot_status": bot.get("status"), "can_post": can_post})

def register_miniapp_routes(app: web.Application):
    r = app.router
    r.add_get("/miniapi/health", health)
    r.add_get("/miniapi/tenants", tenants)
    r.add_get("/miniapi/routes", routes_get)
    r.add_post("/miniapi/routes", routes_post)
    r.add_patch(r"/miniapi/routes/{route_id:\d+}", routes_patch)
    r.add_delete(r"/miniapi/routes/{route_id:\d+}", routes_delete)
    r.add_get("/miniapi/stats", stats_get)
    r.add_get("/miniapi/connectors", connectors_get)
    r.add_post("/miniapi/connectors", connectors_post)
    r.add_get("/miniapi/chat/resolve", chat_resolve)
    r.add_get("/miniapi/chat/check_admin", chat_check_admin)

    # Static HTML fallback (Mini-App-Seite)
    r.add_get("/crossposter-app", crossposter_page)

# ---- Telegram-Handler für /crossposter (WebApp öffnen)
from telegram import Update, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CommandHandler, ContextTypes

async def _miniapp_url() -> str:
    return os.getenv("CROSSPOSTER_MINIAPP_URL") or (os.getenv("APP_BASE_URL","").rstrip("/") + "/crossposter-app")

async def cmd_crossposter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = await _miniapp_url()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🟢 Crossposter öffnen", web_app=WebAppInfo(url=url))]])
    await update.effective_message.reply_text("Crossposter Mini-App", reply_markup=kb)

# Export für bots/crossposter/app.py
crossposter_handler = CommandHandler("crossposter", cmd_crossposter)