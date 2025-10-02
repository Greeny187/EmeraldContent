
import httpx
from typing import List, Optional

API_BASE = "https://api.x.com/2"

class XClientError(Exception):
    pass

async def post_text(access_token: str, text: str, media_ids: Optional[List[str]] = None) -> dict:
    if not access_token:
        raise XClientError("Kein X-Access-Token gesetzt")
    payload = {"text": text}
    if media_ids:
        payload["media"] = {"media_ids": media_ids}
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{API_BASE}/tweets", json=payload, headers=headers)
        if r.status_code >= 400:
            raise XClientError(f"X API Fehler {r.status_code}: {r.text}")
        return r.json()
