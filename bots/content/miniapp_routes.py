from fastapi import APIRouter, Query, Header, HTTPException
from typing import Optional
import hmac, hashlib, urllib.parse, os

router = APIRouter(prefix="/miniapp", tags=["miniapp"])

BOT_TOKEN = os.getenv("BOT1_TOKEN", "")
SECRET = hashlib.sha256(BOT_TOKEN.encode()).digest() if BOT_TOKEN else None

def verify_telegram_init_data(init_data: str) -> bool:
    if not init_data or not SECRET:
        return False
    # Parse & check hash (Telegram WebApp auth)
    parsed = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    data = dict(parsed)
    hash_recv = data.pop("hash", None)
    if not hash_recv:
        return False
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    h = hmac.new(SECRET, msg=data_check_string.encode(), digestmod=hashlib.sha256).hexdigest()
    return h == hash_recv

@router.get("/state")
async def get_state(
    cid: int = Query(..., description="Chat ID (kann negativ sein)"),
    x_telegram_init_data: Optional[str] = Header(None, convert_underscores=False)
):
    # Optional aber empfohlen:
    if x_telegram_init_data and not verify_telegram_init_data(x_telegram_init_data):
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    # TODO: Hole echte Einstellungen aus deiner DB
    # Beispiel: row = await db.fetchrow("SELECT * FROM group_settings WHERE chat_id=$1", cid)
    row = None
    default_settings = {
        "spamfilter_enabled": True,
        "ads_enabled": True,
        "language": "de",
        "features": ["welcome", "farewell", "rss", "ads"]
    }
    exists = row is not None
    settings = default_settings if row is None else dict(row)

    return {
        "chat_id": cid,
        "exists": exists,
        "settings": settings,
        "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z"
    }

@router.post("/save")
async def save_state(
    payload: dict,
    x_telegram_init_data: Optional[str] = Header(None, convert_underscores=False)
):
    if x_telegram_init_data and not verify_telegram_init_data(x_telegram_init_data):
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    cid = int(payload.get("chat_id"))
    settings = payload.get("settings", {})
    # TODO: Persistiere settings
    # await db.execute("UPSERT ...", cid, settings)
    return {"ok": True, "chat_id": cid, "saved": settings}