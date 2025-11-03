import time, hashlib, hmac, base64
from .base import ProviderBase, ProviderCredentials
from .http import session

API_BASE = "https://api.exchange.coinbase.com"

class CoinbaseProvider(ProviderBase):
    id = "coinbase"
    name = "Coinbase Exchange"

    async def ping(self) -> bool:
        # Public /time endpoint
        async with session() as s:
            async with s.get(f"{API_BASE}/time") as r:
                return r.status == 200

    def _sign(self, method: str, path: str, body: str = "") -> dict:
        ts = str(int(time.time()))
        prehash = ts + method.upper() + path + (body or "")
        secret = base64.b64decode(self.creds.api_secret)
        sig = hmac.new(secret, prehash.encode(), hashlib.sha256).digest()
        headers = {
            "CB-ACCESS-KEY": self.creds.api_key,
            "CB-ACCESS-SIGN": base64.b64encode(sig).decode(),
            "CB-ACCESS-TIMESTAMP": ts,
            "CB-ACCESS-PASSPHRASE": (self.creds.passphrase or ""),
            "Content-Type": "application/json",
        }
        return headers

    async def balances(self) -> dict:
        # Private /accounts (Coinbase Exchange)
        path = "/accounts"
        headers = self._sign("GET", path, "")
        async with session() as s:
            async with s.get(f"{API_BASE}{path}", headers=headers) as r:
                j = await r.json(content_type=None)
                if r.status != 200:
                    raise RuntimeError(f"Coinbase error: {j}")
                # Simplify: map currency->balance
                res = {}
                for acc in j:
                    try:
                        c = acc.get("currency"); b = float(acc.get("balance","0"))
                        res[c] = res.get(c, 0.0) + b
                    except Exception:
                        continue
                return res
