import time, hashlib, hmac, base64, urllib.parse
from .base import ProviderBase, ProviderCredentials
from .http import session

API_BASE = "https://api.kraken.com"

class KrakenProvider(ProviderBase):
    id = "kraken"
    name = "Kraken"

    async def ping(self) -> bool:
        # Public SystemStatus
        async with session() as s:
            async with s.get(f"{API_BASE}/0/public/SystemStatus") as r:
                return r.status == 200

    def _sign(self, path: str, data: dict) -> tuple[dict, str]:
        nonce = str(int(time.time()*1000))
        data = {**data, "nonce": nonce}
        postdata = urllib.parse.urlencode(data)
        # Message signature using HMAC-SHA512 of (path + SHA256(nonce+postdata))
        sha = hashlib.sha256((data["nonce"] + postdata).encode()).digest()
        msg = path.encode() + sha
        secret = base64.b64decode(self.creds.api_secret)
        sig = hmac.new(secret, msg, hashlib.sha512).digest()
        headers = {
            "API-Key": self.creds.api_key,
            "API-Sign": base64.b64encode(sig).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        return headers, postdata

    async def balances(self) -> dict:
        # Private Balance endpoint
        path = "/0/private/Balance"
        headers, body = self._sign(path, {})
        async with session() as s:
            async with s.post(f"{API_BASE}{path}", data=body, headers=headers) as r:
                j = await r.json(content_type=None)
                if r.status != 200 or j.get("error"):
                    raise RuntimeError(f"Kraken error: {j.get('error')}")
                return j.get("result", {})
