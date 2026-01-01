"""OKX Exchange API Integration"""

import logging
import httpx
import hmac
import hashlib
import base64
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json

logger = logging.getLogger(__name__)

# OKX API Configuration
OKX_API_URL = "https://www.okx.com/api/v5"
OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"


class OKXProvider:
    """OKX Exchange API Provider"""
    
    def __init__(self, api_key: str, secret_key: str, passphrase: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.base_url = OKX_API_URL
        self.session = None
    
    async def init(self):
        """Initialize HTTP session"""
        self.session = httpx.AsyncClient(timeout=30)
    
    async def close(self):
        """Close HTTP session"""
        if self.session:
            await self.session.aclose()
    
    async def __aenter__(self):
        await self.init()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    def _generate_signature(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """Generate OKX API signature"""
        message = timestamp + method + path + body
        mac = hmac.new(
            bytes(self.secret_key, encoding="utf8"),
            bytes(message, encoding="utf8"),
            digestmod=hashlib.sha256
        )
        d = mac.digest()
        return base64.b64encode(d).decode()
    
    def _get_headers(self, method: str, path: str, body: str = "") -> Dict:
        """Get request headers with signature"""
        timestamp = datetime.utcnow().isoformat() + "Z"
        signature = self._generate_signature(timestamp, method, path, body)
        
        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }
    
    async def get_tickers(self, inst_type: str = "SPOT", inst_family: str = "") -> List[Dict]:
        """Get ticker information for instruments"""
        try:
            path = "/market/tickers"
            params = {"instType": inst_type}
            if inst_family:
                params["instFamily"] = inst_family
            
            async with self.session.get(
                f"{self.base_url}{path}",
                params=params
            ) as resp:
                data = await resp.json()
                if data.get("code") == "0":
                    return data.get("data", [])
                return []
        except Exception as e:
            logger.error(f"OKX get_tickers error: {e}")
            return []
    
    async def get_ticker(self, inst_id: str) -> Optional[Dict]:
        """Get ticker for specific instrument"""
        try:
            path = "/market/ticker"
            async with self.session.get(
                f"{self.base_url}{path}",
                params={"instId": inst_id}
            ) as resp:
                data = await resp.json()
                if data.get("code") == "0" and data.get("data"):
                    return data["data"][0]
                return None
        except Exception as e:
            logger.error(f"OKX get_ticker error: {e}")
            return None
    
    async def get_candlesticks(
        self,
        inst_id: str,
        bar: str = "1H",
        limit: int = 100,
        before: Optional[str] = None,
        after: Optional[str] = None
    ) -> List[Dict]:
        """Get OHLC candlestick data
        
        bar: "1m", "5m", "15m", "30m", "1H", "2H", "4H", "6H", "12H", "1D", "1W", "1M"
        """
        try:
            path = "/market/candles"
            params = {
                "instId": inst_id,
                "bar": bar,
                "limit": min(limit, 300)
            }
            if before:
                params["before"] = before
            if after:
                params["after"] = after
            
            async with self.session.get(
                f"{self.base_url}{path}",
                params=params
            ) as resp:
                data = await resp.json()
                if data.get("code") == "0":
                    return data.get("data", [])
                return []
        except Exception as e:
            logger.error(f"OKX get_candlesticks error: {e}")
            return []
    
    async def get_price_24h_change(self, inst_ids: List[str]) -> Dict[str, Dict]:
        """Get 24h price change for tokens"""
        results = {}
        try:
            inst_id_str = ",".join(inst_ids)
            tickers = await self.get_tickers()
            
            for ticker in tickers:
                if ticker["instId"] in inst_ids:
                    results[ticker["instId"]] = {
                        "last_price": float(ticker.get("last", 0)),
                        "change_24h": float(ticker.get("change24h", 0)),
                        "change_24h_pct": float(ticker.get("changeUtc24h", 0)) * 100,
                        "high_24h": float(ticker.get("high24h", 0)),
                        "low_24h": float(ticker.get("low24h", 0)),
                        "volume_24h": float(ticker.get("vol24h", 0)),
                    }
        except Exception as e:
            logger.error(f"OKX price change error: {e}")
        
        return results
    
    async def get_funding_rate(self, inst_id: str) -> Optional[Dict]:
        """Get current funding rate (for perpetuals)"""
        try:
            path = "/public/funding-rate"
            async with self.session.get(
                f"{self.base_url}{path}",
                params={"instId": inst_id}
            ) as resp:
                data = await resp.json()
                if data.get("code") == "0" and data.get("data"):
                    fr = data["data"][0]
                    return {
                        "funding_rate": float(fr.get("fundingRate", 0)),
                        "next_funding_time": fr.get("nextFundingTime"),
                        "inst_id": inst_id
                    }
                return None
        except Exception as e:
            logger.error(f"OKX funding rate error: {e}")
            return None
    
    async def get_mark_price(self, inst_id: str) -> Optional[float]:
        """Get mark price for an instrument"""
        try:
            path = "/public/mark-price"
            async with self.session.get(
                f"{self.base_url}{path}",
                params={"instId": inst_id}
            ) as resp:
                data = await resp.json()
                if data.get("code") == "0" and data.get("data"):
                    return float(data["data"][0].get("markPx", 0))
                return None
        except Exception as e:
            logger.error(f"OKX mark price error: {e}")
            return None
    
    async def get_depth(self, inst_id: str, sz: int = 1) -> Optional[Dict]:
        """Get order book depth
        
        sz: 1-20 (number of orders)
        """
        try:
            path = "/market/books"
            async with self.session.get(
                f"{self.base_url}{path}",
                params={"instId": inst_id, "sz": min(sz, 20)}
            ) as resp:
                data = await resp.json()
                if data.get("code") == "0" and data.get("data"):
                    return data["data"][0]
                return None
        except Exception as e:
            logger.error(f"OKX depth error: {e}")
            return None
    
    async def get_trading_volume_24h(self, inst_id: str) -> Optional[float]:
        """Get 24h trading volume"""
        try:
            ticker = await self.get_ticker(inst_id)
            if ticker:
                return float(ticker.get("vol24h", 0))
            return None
        except Exception as e:
            logger.error(f"OKX volume error: {e}")
            return None
    
    async def get_spot_trading_pairs(self) -> List[str]:
        """Get all available spot trading pairs"""
        try:
            path = "/public/instruments"
            async with self.session.get(
                f"{self.base_url}{path}",
                params={"instType": "SPOT"}
            ) as resp:
                data = await resp.json()
                if data.get("code") == "0":
                    return [inst["instId"] for inst in data.get("data", [])]
                return []
        except Exception as e:
            logger.error(f"OKX get_spot_pairs error: {e}")
            return []
    
    async def get_account_balance(self) -> Optional[Dict]:
        """Get account balance (requires authentication)"""
        try:
            path = "/account/balance"
            headers = self._get_headers("GET", path)
            
            async with self.session.get(
                f"{self.base_url}{path}",
                headers=headers
            ) as resp:
                data = await resp.json()
                if data.get("code") == "0":
                    return data.get("data", [{}])[0]
                return None
        except Exception as e:
            logger.error(f"OKX balance error: {e}")
            return None
    
    async def get_exchange_rates(self, ccy_pair: str = "BTC-USD") -> Optional[float]:
        """Get exchange rate (e.g., BTC-USD)"""
        try:
            path = "/market/exchange-rate"
            async with self.session.get(
                f"{self.base_url}{path}",
                params={"ccy": ccy_pair.split("-")[0]}
            ) as resp:
                data = await resp.json()
                if data.get("code") == "0":
                    return float(data["data"][0].get("rate", 0))
                return None
        except Exception as e:
            logger.error(f"OKX exchange rate error: {e}")
            return None


async def create_okx_provider(api_key: str, secret_key: str, passphrase: str) -> OKXProvider:
    """Factory function to create and initialize OKX provider"""
    provider = OKXProvider(api_key, secret_key, passphrase)
    await provider.init()
    return provider
