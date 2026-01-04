import os, hmac, hashlib, json, logging
from urllib.parse import unquote
from typing import Dict, Any, Optional

import anyio
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, validator

from . import database as db  # ✅ single DB file

logger = logging.getLogger("bot.support.api")
router = APIRouter(prefix="/api/support", tags=["support"])
BOT_TOKEN = os.getenv("BOT6_TOKEN") or os.getenv("BOT_TOKEN")


def _verify_init_data(init_data: str) -> Dict[str, Any]:
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing X-Telegram-Init-Data")

    pairs = [p for p in init_data.split("&") if "=" in p]
    data = {}
    for p in pairs:
        k, v = p.split("=", 1)
        data[k] = unquote(v)

    hash_recv = data.pop("hash", None)
    if not hash_recv:
        raise HTTPException(status_code=401, detail="Missing hash in init data")

    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data.keys()))

    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not configured")
        raise HTTPException(status_code=500, detail="Server configuration error")

    secret_key = hashlib.sha256(b"WebAppData" + BOT_TOKEN.encode()).digest()
    calc_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc_hash, hash_recv):
        logger.warning("Bad signature attempt")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        user_obj = json.loads(data.get("user", "{}"))
        if not user_obj or "id" not in user_obj:
            raise HTTPException(status_code=401, detail="Missing or invalid user in init data")

        return {
            "user_id": int(user_obj["id"]),
            "username": user_obj.get("username"),
            "first_name": user_obj.get("first_name"),
            "last_name": user_obj.get("last_name"),
            "language_code": user_obj.get("language_code", "de"),
        }
    except Exception:
        logger.exception("Error parsing user from init data")
        raise HTTPException(status_code=401, detail="Invalid init data format")


class TicketCreate(BaseModel):
    category: str = Field(default="allgemein", max_length=48)
    subject: str = Field(..., min_length=4, max_length=140)
    body: str = Field(..., min_length=10, max_length=4000)

    @validator("category")
    def validate_category(cls, v):
        allowed = ["allgemein", "technik", "zahlungen", "konto", "feedback"]
        if v not in allowed:
            raise ValueError(f"Category must be one of {allowed}")
        return v


class TicketReply(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


@router.get("/health")
async def health():
    return {"ok": True, "service": "emerald-support"}


@router.post("/tickets")
async def create_ticket(
    request: Request,
    payload: TicketCreate,
    x_telegram_init_data: str = Header(None),
    cid: Optional[int] = Query(None, description="Chat/Group ID (optional)"),
    title: Optional[str] = Query(None, description="Chat title (optional)"),
):
    try:
        user = _verify_init_data(x_telegram_init_data)

        logger.info(
            "POST /tickets user_id=%s origin=%s cid=%s",
            user["user_id"],
            request.headers.get("origin"),
            cid,
        )

        # init schema best effort (optional – kannst du später rausnehmen)
        await anyio.to_thread.run_sync(db.init_all_schemas)

        ok = await anyio.to_thread.run_sync(db.upsert_user, user)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to upsert user")

        tenant_id = None
        if cid is not None:
            try:
                tenant_id = await anyio.to_thread.run_sync(db.ensure_tenant_for_chat, cid, title, None)
            except Exception:
                logger.exception("ensure_tenant_for_chat failed (continuing unscoped): cid=%s", cid)
                tenant_id = None

        tid = await anyio.to_thread.run_sync(
            db.create_ticket,
            user["user_id"],
            payload.category,
            payload.subject,
            payload.body,
            tenant_id,
        )

        if not tid:
            raise HTTPException(status_code=500, detail="Failed to create ticket")

        return {"ok": True, "ticket_id": tid}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error creating ticket")
        raise HTTPException(status_code=500, detail="Failed to create ticket")


@router.get("/tickets")
async def list_my_tickets(
    request: Request,
    x_telegram_init_data: str = Header(None),
    cid: Optional[int] = Query(None),
    limit: int = Query(30, ge=1, le=100),
):
    try:
        user = _verify_init_data(x_telegram_init_data)
        logger.info(
            "GET /tickets user_id=%s origin=%s cid=%s",
            user["user_id"],
            request.headers.get("origin"),
            cid,
        )

        tenant_id = None
        if cid is not None:
            tenant_id = await anyio.to_thread.run_sync(db.resolve_tenant_id_by_chat, cid)

        tickets = await anyio.to_thread.run_sync(db.get_my_tickets, user["user_id"], limit, tenant_id)
        return {"ok": True, "tickets": tickets or []}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error listing tickets")
        raise HTTPException(status_code=500, detail="Failed to list tickets")


@router.get("/tickets/{ticket_id}")
async def get_ticket(
    request: Request,
    ticket_id: int,
    x_telegram_init_data: str = Header(None),
):
    try:
        user = _verify_init_data(x_telegram_init_data)
        logger.info(
            "GET /tickets/%s user_id=%s origin=%s",
            ticket_id,
            user["user_id"],
            request.headers.get("origin"),
        )

        ticket = await anyio.to_thread.run_sync(db.get_ticket, user["user_id"], ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found or access denied")
        return {"ok": True, "ticket": ticket}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error fetching ticket")
        raise HTTPException(status_code=500, detail="Failed to fetch ticket")


@router.post("/tickets/{ticket_id}/messages")
async def add_message(
    request: Request,
    ticket_id: int,
    payload: TicketReply,
    x_telegram_init_data: str = Header(None),
):
    try:
        user = _verify_init_data(x_telegram_init_data)
        logger.info(
            "POST /tickets/%s/messages user_id=%s origin=%s",
            ticket_id,
            user["user_id"],
            request.headers.get("origin"),
        )

        ok = await anyio.to_thread.run_sync(db.add_public_message, user["user_id"], ticket_id, payload.text)
        if not ok:
            raise HTTPException(status_code=403, detail="Not allowed to add message to this ticket")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error adding message")
        raise HTTPException(status_code=500, detail="Failed to add message")


@router.get("/kb/search")
async def kb_search(
    request: Request,
    q: str = Query(..., min_length=2),
    limit: int = Query(8, ge=1, le=20),
    x_telegram_init_data: str = Header(None),
):
    try:
        user = _verify_init_data(x_telegram_init_data)
        logger.info(
            "GET /kb/search user_id=%s origin=%s qlen=%s",
            user["user_id"],
            request.headers.get("origin"),
            len(q),
        )

        results = await anyio.to_thread.run_sync(db.kb_search, q, limit)
        return {"ok": True, "results": results or []}
    except HTTPException:
        raise
    except Exception:
        logger.exception("KB search failed")
        raise HTTPException(status_code=500, detail="KB search failed")
