
# bots/content/miniapp_crossposter.py
# PTB-Command + FastAPI-API (mandantenfähig).

import hmac, hashlib, json, time, os
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, ChatMember
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ChatMemberStatus

from .database import get_pool
from .models import user_in_tenant, list_tenants_for_user, create_route, update_route, delete_route, list_routes, stats

MINIAPP_URL = os.environ.get("CROSSPOSTER_MINIAPP_URL", "https://example.com/miniapp/crossposter.html")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "SET_ME")

# ---------- PTB: /crossposter
async def cmd_crossposter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(text="Crossposter öffnen", web_app=WebAppInfo(url=MINIAPP_URL))]])
    if update.message:
        await update.message.reply_text("Öffne die Crossposter MiniApp.", reply_markup=kb)

crossposter_handler = CommandHandler("crossposter", cmd_crossposter)

# ---------- FastAPI
API = FastAPI(title="Emerald Crossposter API", version="0.1-mt")

# Telegram initData Verify
def verify_init_data(init_data: str, bot_token: str) -> Dict[str, Any]:
    try:
        from urllib.parse import parse_qsl
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        if 'hash' not in data:
            raise ValueError('hash missing')
        check_hash = data.pop('hash')
        secret_key = hashlib.sha256(bot_token.encode()).digest()
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(data.items()))
        h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if h != check_hash:
            raise ValueError('bad hash')
        if 'auth_date' in data and (time.time() - int(data['auth_date'])) > 86400:
            raise ValueError('stale auth')
        user = json.loads(data.get('user', '{}'))
        data['user'] = user
        return data
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"initData invalid: {e}")

# Models
class Destination(BaseModel):
    type: str = Field(example="telegram")
    chat_id: Optional[int] = None

class RouteIn(BaseModel):
    tenant_id: int
    source_chat_id: int
    destinations: List[Destination]
    transform: Dict[str, Any] = Field(default_factory=lambda: {"prefix":"","suffix":"","plain_text":False})
    filters: Dict[str, Any] = Field(default_factory=lambda: {"hashtags_whitelist":[],"hashtags_blacklist":[]})
    active: bool = True

class RouteOut(RouteIn):
    id: int

# Helper: Admin-Check
async def is_admin(context_bot, user_id: int, chat_id: int) -> bool:
    try:
        member: ChatMember = await context_bot.getChatMember(chat_id, user_id)
        return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
    except Exception:
        return False

# Dependencies
async def current_user(x_telegram_init_data: str = Header(...)):
    return verify_init_data(x_telegram_init_data, BOT_TOKEN)

@API.get("/health")
async def health():
    return {"ok": True}

@API.get("/tenants")
async def get_tenants(user=Depends(current_user)):
    rows = await list_tenants_for_user(user['user']['id'])
    return [dict(r) for r in rows]

@API.get("/routes", response_model=List[RouteOut])
async def list_routes_api(tenant_id: int = Query(...), user=Depends(current_user)):
    # Mitgliedschaft prüfen
    if not await user_in_tenant(tenant_id, user['user']['id']):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Mandanten")
    rows = await list_routes(tenant_id, user['user']['id'])
    # Pydantic-Serialisierung
    out = []
    for r in rows:
        out.append(RouteOut(
            id=r["id"],
            tenant_id=tenant_id,
            source_chat_id=r["source_chat_id"],
            destinations=r["destinations"],
            transform=r["transform"],
            filters=r["filters"],
            active=r["active"]
        ))
    return out

@API.post("/routes", response_model=RouteOut)
async def create_route_api(payload: RouteIn, user=Depends(current_user)):
    # Mitgliedschaft + Admin-Gate in der Quelle
    if not await user_in_tenant(payload.tenant_id, user['user']['id']):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Mandanten")
    # Lazy Import um Zyklen zu vermeiden
    ok = await is_admin(app_bot, user['user']['id'], payload.source_chat_id)
    if not ok:
        raise HTTPException(status_code=403, detail="Kein Admin in der Quellgruppe")
    row = await create_route(payload.tenant_id, user['user']['id'], payload.source_chat_id,
                             json.loads(payload.json())['destinations'], payload.transform, payload.filters, payload.active)
    return RouteOut(
        id=row["id"],
        tenant_id=row["tenant_id"],
        source_chat_id=row["source_chat_id"],
        destinations=row["destinations"],
        transform=row["transform"],
        filters=row["filters"],
        active=row["active"]
    )

@API.patch("/routes/{route_id}", response_model=RouteOut)
async def update_route_api(route_id: int, payload: RouteIn, user=Depends(current_user)):
    if not await user_in_tenant(payload.tenant_id, user['user']['id']):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Mandanten")
    row = await update_route(route_id, payload.tenant_id, user['user']['id'],
                             payload.source_chat_id, json.loads(payload.json())['destinations'],
                             payload.transform, payload.filters, payload.active)
    if not row:
        raise HTTPException(status_code=404, detail="Route nicht gefunden")
    return RouteOut(
        id=row["id"],
        tenant_id=row["tenant_id"],
        source_chat_id=row["source_chat_id"],
        destinations=row["destinations"],
        transform=row["transform"],
        filters=row["filters"],
        active=row["active"]
    )

@API.delete("/routes/{route_id}")
async def delete_route_api(route_id: int, tenant_id: int = Query(...), user=Depends(current_user)):
    if not await user_in_tenant(tenant_id, user['user']['id']):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Mandanten")
    await delete_route(route_id, tenant_id, user['user']['id'])
    return {"ok": True}

@API.get("/stats")
async def stats_api(tenant_id: int = Query(...), user=Depends(current_user)):
    if not await user_in_tenant(tenant_id, user['user']['id']):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Mandanten")
    total, by_status = await stats(tenant_id, user['user']['id'])
    return {"routes": total, "by_status": by_status}
