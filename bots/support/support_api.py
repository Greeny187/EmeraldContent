# support_api.py — Support Bot API (v1.0 - Production Ready)
import os, hmac, hashlib, json, logging
from urllib.parse import unquote
from fastapi import APIRouter, Header, HTTPException, Query, Body
from pydantic import BaseModel, Field, validator
from typing import Dict, Any, Optional, List
from datetime import datetime
import sql as store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/support", tags=["support"])
BOT_TOKEN = os.getenv("BOT6_TOKEN") or os.getenv("BOT_TOKEN")

def _verify_init_data(init_data: str) -> Dict[str, Any]:
    """Verify Telegram WebApp init data"""
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
        logger.warning(f"Bad signature attempt")
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
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"Error parsing user from init data: {e}")
        raise HTTPException(status_code=401, detail="Invalid init data format")

# --- Models ---
class TicketCreate(BaseModel):
    """Create ticket request"""
    category: str = Field(default="allgemein", max_length=48)
    subject: str = Field(..., min_length=4, max_length=140)
    body: str = Field(..., min_length=10, max_length=4000)
    
    @validator('category')
    def validate_category(cls, v):
        allowed = ["allgemein", "technik", "zahlungen", "konto", "feedback"]
        if v not in allowed:
            raise ValueError(f"Category must be one of {allowed}")
        return v

class TicketReply(BaseModel):
    """Reply to ticket"""
    text: str = Field(..., min_length=1, max_length=4000)

class TicketResponse(BaseModel):
    """Ticket response"""
    id: int
    category: str
    subject: str
    status: str
    created_at: str
    closed_at: Optional[str] = None

# --- Endpoints ---

@router.post("/tickets")
async def create_ticket(
    payload: TicketCreate,
    x_telegram_init_data: str = Header(None)
):
    """Create new support ticket"""
    try:
        if not store:
            raise HTTPException(status_code=503, detail="Support system unavailable")
        
        user = _verify_init_data(x_telegram_init_data)
        
        # Upsert user
        await store.upsert_user(user)
        
        # Create ticket
        tid = await store.create_ticket(
            user_id=user["user_id"],
            category=payload.category,
            subject=payload.subject,
            body=payload.body
        )
        
        if not tid:
            raise HTTPException(status_code=500, detail="Failed to create ticket")
        
        logger.info(f"✅ Ticket #{tid} created by user {user['user_id']}")
        return {"ok": True, "ticket_id": tid}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error creating ticket: {e}")
        raise HTTPException(status_code=500, detail="Failed to create ticket")

@router.get("/tickets")
async def list_my_tickets(
    x_telegram_init_data: str = Header(None),
    cid: Optional[int] = Query(None),
    limit: int = Query(30, ge=1, le=100)
):
    """List user's support tickets"""
    try:
        if not store:
            raise HTTPException(status_code=503, detail="Support system unavailable")
        
        user = _verify_init_data(x_telegram_init_data)
        
        tenant_id = None
        if cid:
            tenant_id = await store.resolve_tenant_id_by_chat(cid)
        
        tickets = await store.get_my_tickets(user["user_id"], limit=limit, tenant_id=tenant_id)
        logger.info(f"User {user['user_id']} fetched {len(tickets)} tickets")
        return {"ok": True, "tickets": tickets or []}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error listing tickets: {e}")
        raise HTTPException(status_code=500, detail="Failed to list tickets")

@router.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: int,
    x_telegram_init_data: str = Header(None)
):
    """Fetch single ticket with messages"""
    try:
        if not store:
            raise HTTPException(status_code=503, detail="Support system unavailable")
        
        user = _verify_init_data(x_telegram_init_data)
        ticket = await store.get_ticket(user["user_id"], ticket_id)
        
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found or access denied")
        
        return {"ok": True, "ticket": ticket}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error fetching ticket {ticket_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch ticket")

@router.post("/tickets/{ticket_id}/messages")
async def add_message(
    ticket_id: int,
    payload: TicketReply,
    x_telegram_init_data: str = Header(None)
):
    """Add reply to ticket"""
    try:
        if not store:
            raise HTTPException(status_code=503, detail="Support system unavailable")
        
        user = _verify_init_data(x_telegram_init_data)
        ok = await store.add_public_message(user["user_id"], ticket_id, payload.text)
        
        if not ok:
            raise HTTPException(status_code=403, detail="Cannot add message to this ticket")
        
        logger.info(f"✅ Message added to ticket #{ticket_id} by user {user['user_id']}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error adding message to ticket {ticket_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add message")

@router.get("/kb/search")
async def kb_search(
    q: str = Query(..., min_length=2),
    limit: int = Query(8, ge=1, le=20),
    x_telegram_init_data: str = Header(None)
):
    """Search knowledge base"""
    try:
        if not store:
            raise HTTPException(status_code=503, detail="Support system unavailable")
        
        user = _verify_init_data(x_telegram_init_data)
        results = await store.kb_search(q, limit=limit)
        
        logger.debug(f"KB search '{q}' by user {user['user_id']}: {len(results)} results")
        return {"ok": True, "results": results or []}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error searching KB: {e}")
        raise HTTPException(status_code=500, detail="KB search failed")

# --- MiniApp Admin Endpoints ---

@router.get("/miniapp/state")
async def miniapp_state(
    cid: int = Query(...),
    uid: int = Query(...),
    x_telegram_init_data: str = Header(None)
):
    """Load group settings for MiniApp"""
    try:
        if not store:
            raise HTTPException(status_code=503, detail="Support system unavailable")
        
        _ = _verify_init_data(x_telegram_init_data)
        data = await store.load_group_settings(cid)
        
        return {
            "welcome": {
                "on": bool(data.get("welcome_on")),
                "text": data.get("welcome_text", "")
            },
            "farewell": {
                "on": bool(data.get("farewell_on")),
                "text": data.get("farewell_text", "")
            },
            "rules": {
                "on": bool(data.get("rules_on")),
                "text": data.get("rules_text", "")
            },
            "links": {
                "only_admin_links": bool(data.get("admins_only")),
                "warning_enabled": bool(data.get("warning_on")),
                "warning_text": data.get("warning_text", ""),
                "exceptions_enabled": bool(data.get("exceptions_on")),
            },
            "ai": {
                "faq": bool(data.get("ai_faq", True)),
                "rss": bool(data.get("ai_rss", False)),
            },
            "mood": {
                "topic": data.get("mood_topic", ""),
                "question": data.get("mood_question", "Wie war dein Tag von 1–5?")
            },
            "daily_stats": bool(data.get("daily_stats", False))
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error loading state for chat {cid}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load settings")

@router.post("/miniapp/state")
async def save_miniapp_state(
    cid: int = Query(...),
    x_telegram_init_data: str = Header(None),
    body: dict = Body(...)
):
    """Save group settings from MiniApp"""
    try:
        if not store or not body:
            raise HTTPException(status_code=503, detail="Support system unavailable")
        
        user = _verify_init_data(x_telegram_init_data)
        
        # Ensure tenant exists
        await store.ensure_tenant_for_chat(
            chat_id=cid,
            title=body.get("title")
        )
        
        # Save settings
        ok = await store.save_group_settings(
            chat_id=cid,
            title=body.get("title"),
            data=body,
            updated_by=user["user_id"]
        )
        
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to save settings")
        
        logger.info(f"✅ Group settings saved for chat {cid} by user {user['user_id']}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error saving state for chat {cid}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save settings")

@router.get("/miniapp/stats")
async def miniapp_stats(
    cid: int = Query(...),
    uid: int = Query(...),
    days: int = Query(14, ge=1, le=60),
    x_telegram_init_data: str = Header(None)
):
    """Load group statistics"""
    try:
        if not store:
            raise HTTPException(status_code=503, detail="Support system unavailable")
        
        _ = _verify_init_data(x_telegram_init_data)
        agg = await store.load_group_stats(cid, days=days)
        settings = await store.load_group_settings(cid)
        
        return {
            "ok": True,
            "daily_stats_enabled": bool(settings.get("daily_stats", False)),
            "agg": agg or [],
            "top_responders": []
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error loading stats for chat {cid}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load statistics")


@router.post("/tickets/{ticket_id}/messages")
async def add_message(
    ticket_id: int,
    payload: TicketReply,
    x_telegram_init_data: str = Header(None)
):
    """Antwort zu Ticket hinzufügen"""
    if not store:
        raise HTTPException(status_code=503, detail="Support system unavailable")
    
    try:
        user = _verify_init_data(x_telegram_init_data)
        ok = await store.add_public_message(user["user_id"], ticket_id, payload.text)
        
        if not ok:
            raise HTTPException(status_code=403, detail="Not allowed to add message to this ticket")
        
        logger.info(f"Message added to ticket #{ticket_id} by user {user['user_id']}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error adding message to ticket {ticket_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/kb/search")
async def kb_search(
    q: str = Query(..., min_length=2, description="Suchbegriff"),
    limit: int = Query(8, ge=1, le=20),
    x_telegram_init_data: str = Header(None)
):
    """Knowledge Base durchsuchen"""
    if not store:
        raise HTTPException(status_code=503, detail="Support system unavailable")
    
    try:
        user = _verify_init_data(x_telegram_init_data)
        results = await store.kb_search(q, limit=limit)
        
        logger.info(f"KB search '{q}' by user {user['user_id']}: {len(results)} results")
        return {"ok": True, "results": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error searching KB: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- MiniApp Admin Endpoints (für Group/Tenant Setup) ---

@router.get("/miniapp/state")
async def miniapp_state(
    cid: int = Query(..., description="Chat/Gruppen-ID"),
    uid: int = Query(..., description="Telegram User-ID"),
    x_telegram_init_data: str = Header(None),
):
    """Lade gespeicherte Group-Settings als JSON (für MiniApp-Form)"""
    if not store:
        raise HTTPException(status_code=503, detail="Support system unavailable")
    
    try:
        _ = _verify_init_data(x_telegram_init_data)
        data = await store.load_group_settings(cid)
        
        # Standardwerte mergen
        resp = {
            "welcome": {
                "on": bool(data.get("welcome_on")),
                "text": data.get("welcome_text", "")
            },
            "farewell": {
                "on": bool(data.get("farewell_on")),
                "text": data.get("farewell_text", "")
            },
            "rules": {
                "on": bool(data.get("rules_on")),
                "text": data.get("rules_text", "")
            },
            "links": {
                "only_admin_links": bool(data.get("admins_only")),
                "warning_enabled": bool(data.get("warning_on")),
                "warning_text": data.get("warning_text", ""),
                "exceptions_enabled": bool(data.get("exceptions_on")),
            },
            "ai": {
                "faq": bool(data.get("ai_faq", True)),
                "rss": bool(data.get("ai_rss", False)),
            },
            "mood": {
                "topic": data.get("mood_topic", ""),
                "question": data.get("mood_question", "Wie war dein Tag von 1–5?")
            },
            "daily_stats": bool(data.get("daily_stats", False))
        }
        return resp
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error loading state for chat {cid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/miniapp/state")
async def save_miniapp_state(
    cid: int = Query(...),
    x_telegram_init_data: str = Header(None),
    body: dict = None
):
    """Speichere Group-Settings (von MiniApp)"""
    if not store or not body:
        raise HTTPException(status_code=503, detail="Support system unavailable")
    
    try:
        user = _verify_init_data(x_telegram_init_data)
        
        # Stelle sicher dass Tenant existiert
        tenant_id = await store.ensure_tenant_for_chat(
            chat_id=cid,
            title=body.get("title")
        )
        
        # Speichere Settings
        ok = await store.save_group_settings(
            chat_id=cid,
            title=body.get("title"),
            data=body,
            updated_by=user["user_id"]
        )
        
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to save settings")
        
        logger.info(f"Group settings saved for chat {cid} by user {user['user_id']}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error saving state for chat {cid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/miniapp/stats")
async def miniapp_stats(
    cid: int = Query(...),
    uid: int = Query(...),
    days: int = Query(14, ge=1, le=60),
    x_telegram_init_data: str = Header(None),
):
    """Lade Gruppen-Statistiken für Dashboard"""
    if not store:
        raise HTTPException(status_code=503, detail="Support system unavailable")
    
    try:
        _ = _verify_init_data(x_telegram_init_data)
        agg = await store.load_group_stats(cid, days=days)
        settings = await store.load_group_settings(cid)
        
        return {
            "ok": True,
            "daily_stats_enabled": bool(settings.get("daily_stats", False)),
            "agg": agg,
            "top_responders": []  # TODO: Später implementieren
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error loading stats for chat {cid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
