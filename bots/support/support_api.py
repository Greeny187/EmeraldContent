import os, hmac, hashlib, json
from urllib.parse import unquote
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


from support_bot.storage import sql as store


router = APIRouter(prefix="/api/support", tags=["support"])
BOT_TOKEN = os.getenv('BOT_TOKEN')


# --- Telegram WebApp initData Verification ---
# Spec: https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app


def _verify_init_data(init_data: str) -> Dict[str, Any]:
if not init_data:
raise HTTPException(status_code=401, detail='Missing X-Telegram-Init-Data')


# parse pairs
pairs = [p for p in init_data.split('&') if '=' in p]
data = {}
for p in pairs:
k, v = p.split('=', 1)
data[k] = unquote(v)


hash_recv = data.pop('hash', None)
if not hash_recv:
raise HTTPException(401, 'Missing hash')


check_string = '\n'.join(f"{k}={data[k]}" for k in sorted(data.keys()))


if not BOT_TOKEN:
raise HTTPException(500, 'BOT_TOKEN not configured')


secret_key = hashlib.sha256("WebAppData".encode() + BOT_TOKEN.encode()).digest()
calc_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()


if not hmac.compare_digest(calc_hash, hash_recv):
raise HTTPException(401, 'Bad signature')


user_obj = json.loads(data.get('user', '{}'))
if not user_obj or 'id' not in user_obj:
raise HTTPException(401, 'Missing user in init data')


return {
'user_id': int(user_obj['id']),
'username': user_obj.get('username'),
'first_name': user_obj.get('first_name'),
'last_name': user_obj.get('last_name'),
'language_code': user_obj.get('language_code')
}


# --- Models ---
class TicketCreate(BaseModel):
category: str = Field(default='allgemein', max_length=48)
subject: str = Field(..., min_length=4, max_length=140)
body: str = Field(..., min_length=10, max_length=4000)


class TicketReply(BaseModel):
text: str = Field(..., min_length=1, max_length=4000)


# --- Routes ---
@router.post('/tickets')
async def create_ticket(payload: TicketCreate, x_telegram_init_data: str = Header(None)):
user = _verify_init_data(x_telegram_init_data)
await store.upsert_user(user)
tid = await store.create_ticket(user_id=user['user_id'], category=payload.category, subject=payload.subject, body=payload.body)
return {'ok': True, 'ticket_id': tid}


@router.get('/tickets')
async def list_my_tickets(x_telegram_init_data: str = Header(None)):
user = _verify_init_data(x_telegram_init_data)
tickets = await store.get_my_tickets(user['user_id'])
return {'ok': True, 'tickets': tickets}


@router.get('/tickets/{ticket_id}')
async def get_ticket(ticket_id: int, x_telegram_init_data: str = Header(None)):
user = _verify_init_data(x_telegram_init_data)
t = await store.get_ticket(user['user_id'], ticket_id)
if not t:
raise HTTPException(404, 'Ticket not found')
return {'ok': True, 'ticket': t}


@router.post('/tickets/{ticket_id}/messages')
async def add_message(ticket_id: int, payload: TicketReply, x_telegram_init_data: str = Header(None)):
user = _verify_init_data(x_telegram_init_data)
ok = await store.add_public_message(user['user_id'], ticket_id, payload.text)
if not ok:
raise HTTPException(403, 'Not allowed')
return {'ok': True}


@router.get('/kb/search')
async def kb_search(q: str, x_telegram_init_data: str = Header(None)):
_ = _verify_init_data(x_telegram_init_data)
results = await store.kb_search(q)
return {'ok': True, 'results': results}