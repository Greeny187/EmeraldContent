import time, hmac, hashlib, urllib.parse
from .base import ProviderBase, ProviderCredentials
from .http import session

API_BASE = "https://api.mexc.com"

class MexcProvider(ProviderBase):
    id = "mexc"
    name = "MEXC"

    async def ping(self) -> bool:
        async with session() as s:
            async with s.get(f"{API_BASE}/api/v3/ping") as r:
                return r.status == 200

    def _signed_params(self, params: dict) -> tuple[str, dict]:
        params = {**params, "timestamp": int(time.time()*1000)}
        qs = urllib.parse.urlencode(params, doseq=True)
        sig = hmac.new(self.creds.api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        qs += f"&signature={sig}"
        headers = {"X-MEXC-APIKEY": self.creds.api_key}
        return qs, headers

    async def balances(self) -> dict:
        # Private /api/v3/account
        qs, headers = self._signed_params({})
        async with session() as s:
            async with s.get(f"{API_BASE}/api/v3/account?{qs}", headers=headers) as r:
                j = await r.json(content_type=None)
                if r.status != 200:
                    raise RuntimeError(f"MEXC error: {j}")
                res = {}
                for b in j.get("balances", []):
                    try:
                        asset = b.get("asset"); free = float(b.get("free","0"))
                        if free: res[asset] = free
                    except Exception:
                        continue
                return res
