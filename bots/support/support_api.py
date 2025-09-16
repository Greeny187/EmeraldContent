# support_api.py — bereinigt & erweitert
import os, hmac, hashlib, json
from urllib.parse import unquote
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Dict, Any

import sql as store  # <— lokal importieren

router = APIRouter(prefix="/api/support", tags=["support"])
BOT_TOKEN = os.getenv("BOT_TOKEN")

def _verify_init_data(init_data: str) -> Dict[str, Any]:
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing X-Telegram-Init-Data")

    # parse pairs
    pairs = [p for p in init_data.split("&") if "=" in p]
    data = {}
    for p in pairs:
        k, v = p.split("=", 1)
        data[k] = unquote(v)

    hash_recv = data.pop("hash", None)
    if not hash_recv:
        raise HTTPException(status_code=401, detail="Missing hash")

    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data.keys()))

    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not configured")

    secret_key = hashlib.sha256(b"WebAppData" + BOT_TOKEN.encode()).digest()
    calc_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc_hash, hash_recv):
        raise HTTPException(status_code=401, detail="Bad signature")

    user_obj = json.loads(data.get("user", "{}"))
    if not user_obj or "id" not in user_obj:
        raise HTTPException(status_code=401, detail="Missing user in init data")

    return {
        "user_id": int(user_obj["id"]),
        "username": user_obj.get("username"),
        "first_name": user_obj.get("first_name"),
        "last_name": user_obj.get("last_name"),
        "language_code": user_obj.get("language_code"),
    }

# --- Models ---
class TicketCreate(BaseModel):
    category: str = Field(default="allgemein", max_length=48)
    subject: str = Field(..., min_length=4, max_length=140)
    body: str = Field(..., min_length=10, max_length=4000)

class TicketReply(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)

# --- Support (bestehend) ---
@router.post("/tickets")
async def create_ticket(payload: TicketCreate, x_telegram_init_data: str = Header(None)):
    user = _verify_init_data(x_telegram_init_data)
    await store.upsert_user(user)
    tid = await store.create_ticket(
        user_id=user["user_id"], category=payload.category, subject=payload.subject, body=payload.body
    )
    return {"ok": True, "ticket_id": tid}

@router.get("/tickets")
async def list_my_tickets(x_telegram_init_data: str = Header(None)):
    user = _verify_init_data(x_telegram_init_data)
    tickets = await store.get_my_tickets(user["user_id"])
    return {"ok": True, "tickets": tickets}

@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: int, x_telegram_init_data: str = Header(None)):
    user = _verify_init_data(x_telegram_init_data)
    t = await store.get_ticket(user["user_id"], ticket_id)
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"ok": True, "ticket": t}

@router.post("/tickets/{ticket_id}/messages")
async def add_message(ticket_id: int, payload: TicketReply, x_telegram_init_data: str = Header(None)):
    user = _verify_init_data(x_telegram_init_data)
    ok = await store.add_public_message(user["user_id"], ticket_id, payload.text)
    if not ok:
        raise HTTPException(status_code=403, detail="Not allowed")
    return {"ok": True}

@router.get("/kb/search")
async def kb_search(q: str, x_telegram_init_data: str = Header(None)):
    _ = _verify_init_data(x_telegram_init_data)
    results = await store.kb_search(q)
    return {"ok": True, "results": results}

# --- MiniApp Endpunkte wie von appsupport.html erwartet ---
# Aufruf in HTML: `${apiBase}/miniapp/state?cid=...&uid=...`
@router.get("/miniapp/state")
async def miniapp_state(
    cid: int = Query(..., description="Chat/Gruppen-ID"),
    uid: int = Query(..., description="Telegram User-ID"),
    x_telegram_init_data: str = Header(None),
):
    _ = _verify_init_data(x_telegram_init_data)  # prüft Signatur
    # Lade gespeicherte Settings (JSON)
    data = await store.load_group_settings(cid)

    # Map auf die Felder, die appsupport.html setzt/erwartet
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

@router.get("/miniapp/stats")
async def miniapp_stats(
    cid: int = Query(...),
    uid: int = Query(...),
    days: int = Query(14, ge=1, le=60),
    x_telegram_init_data: str = Header(None),
):
    _ = _verify_init_data(x_telegram_init_data)
    agg = await store.load_group_stats(cid, days=days)
    # daily_stats_enabled aus Settings ableiten
    settings = await store.load_group_settings(cid)
    daily_stats_enabled = bool(settings.get("daily_stats", False))

    # Optional: top responder – falls nicht vorhanden, leeres Array
    top_responders = []  # später via eigener Tabelle füllen

    return {
        "daily_stats_enabled": daily_stats_enabled,
        "agg": agg,
        "top_responders": top_responders
    }
