"""Aerodome DEX Integration (Evmos/Cosmos)"""

import logging
import httpx
import json
from typing import Dict, List, Optional
from decimal import Decimal
import asyncio

logger = logging.getLogger(__name__)

# Aerodome API and configuration
AERODOME_API = "https://api.aerodome.exchange"
AERODOME_GRAPH = "https://api.thegraph.com/subgraphs/name/aerodome/aerodome"
EVMOS_RPC = "https://evmos-mainnet-lcd.allthatnode.com"
EVMOS_CHAIN_ID = "evmos_9001-1"

# Popular token addresses on Evmos
WEVMOS = "0xD4949664B2B213f63C1f31b50F6676666A4D4472"  # Wrapped Evmos
USDC = "0x2Cc254Ac582A8dB798674C13A6c40407950d4A10"
USDT = "0x7FF4a56B32ee2e6280078E92b6D14e5EAA2a0B81"
ATOM = "0xB00C137F5f1e4D23B00aEF50cdfFDeF192e7B85a"
EMRD = "0x0000000000000000000000000000000000000000"  # Will be set from config


class AerodomeProvider:
    """Aerodome DEX Provider for Evmos"""
    
    def __init__(self, evmos_rpc: str = EVMOS_RPC, emrd_token: Optional[str] = None):
        self.api_url = AERODOME_API
        self.graph = AERODOME_GRAPH
        self.evmos_rpc = evmos_rpc
        self.emrd_token = emrd_token or EMRD
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
    
    async def get_pools(self) -> List[Dict]:
        """Get all liquidity pools on Aerodome"""
        try:
            query = """
            {
                pairs(first: 100, orderBy: reserveUSD, orderDirection: desc) {
                    id
                    token0 { id symbol decimals }
                    token1 { id symbol decimals }
                    reserve0
                    reserve1
                    reserveUSD
                    volumeUSD
                    feesUSD
                    apr
                }
            }
            """
            
            async with self.session.post(
                self.graph,
                json={"query": query}
            ) as resp:
                data = await resp.json()
                if "data" in data and "pairs" in data["data"]:
                    return data["data"]["pairs"]
                return []
        except Exception as e:
            logger.error(f"Aerodome pools error: {e}")
            return []
    
    async def get_pool_by_tokens(self, token_a: str, token_b: str) -> Optional[Dict]:
        """Get specific pool by token pair"""
        try:
            query = f"""
            {{
                pairs(where: {{
                    token0: "{token_a.lower()}"
                    token1: "{token_b.lower()}"
                }}) {{
                    id
                    token0 {{ id symbol decimals }}
                    token1 {{ id symbol decimals }}
                    reserve0
                    reserve1
                    reserveUSD
                    volumeUSD
                    feesUSD
                    apr
                }}
            }}
            """
            
            async with self.session.post(
                self.graph,
                json={"query": query}
            ) as resp:
                data = await resp.json()
                if data.get("data", {}).get("pairs"):
                    return data["data"]["pairs"][0]
                return None
        except Exception as e:
            logger.error(f"Aerodome get_pool_by_tokens error: {e}")
            return None
    
    async def get_token_price(self, token: str, vs_currency: str = "usd") -> Optional[float]:
        """Get token price"""
        try:
            # Try CoinGecko for most tokens
            async with self.session.get(
                f"https://api.coingecko.com/api/v3/simple/token_price/evmos",
                params={
                    "contract_addresses": token.lower(),
                    "vs_currencies": vs_currency
                }
            ) as resp:
                data = await resp.json()
                if token.lower() in data:
                    return float(data[token.lower()][vs_currency])
        except Exception as e:
            logger.warning(f"Aerodome price fetch (CoinGecko) error: {e}")
        
        return None
    
    async def get_prices_batch(self, tokens: List[str]) -> Dict[str, float]:
        """Get prices for multiple tokens"""
        prices = {}
        
        try:
            token_list = ",".join([t.lower() for t in tokens])
            async with self.session.get(
                f"https://api.coingecko.com/api/v3/simple/token_price/evmos",
                params={
                    "contract_addresses": token_list,
                    "vs_currencies": "usd"
                }
            ) as resp:
                data = await resp.json()
                for token in tokens:
                    if token.lower() in data:
                        prices[token.lower()] = float(data[token.lower()]["usd"])
        except Exception as e:
            logger.error(f"Aerodome batch price error: {e}")
        
        return prices
    
    async def calculate_swap_output(
        self,
        token_in: str,
        token_out: str,
        amount_in: Decimal,
        slippage: float = 0.5
    ) -> Dict:
        """Calculate output amount with price impact"""
        try:
            pool = await self.get_pool_by_tokens(token_in, token_out)
            if not pool:
                return {"error": "Pool not found"}
            
            reserve_in = Decimal(pool["reserve0"])
            reserve_out = Decimal(pool["reserve1"])
            
            # Aerodome typically has 0.3% fee
            amount_in_with_fee = amount_in * Decimal("0.997")
            
            amount_out = (reserve_out * amount_in_with_fee) / (reserve_in + amount_in_with_fee)
            price_impact = (amount_in / (reserve_in + amount_in)) * 100
            min_amount = amount_out * (Decimal(100 - slippage) / Decimal(100))
            
            return {
                "amount_out": str(amount_out),
                "min_amount_out": str(min_amount),
                "price_impact": float(price_impact),
                "slippage": slippage,
                "fee": str(amount_in * Decimal("0.003"))
            }
        except Exception as e:
            logger.error(f"Aerodome calculate_swap error: {e}")
            return {"error": str(e)}
    
    async def get_user_liquidity_positions(self, wallet_address: str) -> List[Dict]:
        """Get user's liquidity positions"""
        try:
            query = f"""
            {{
                liquidityPositions(where: {{ user: "{wallet_address.lower()}" }}) {{
                    id
                    pair {{ id token0 {{ symbol }} token1 {{ symbol }} }}
                    liquidityTokenBalance
                    valueUSD
                }}
            }}
            """
            
            async with self.session.post(
                self.graph,
                json={"query": query}
            ) as resp:
                data = await resp.json()
                if "data" in data and "liquidityPositions" in data["data"]:
                    return data["data"]["liquidityPositions"]
                return []
        except Exception as e:
            logger.error(f"Aerodome positions error: {e}")
            return []
    
    async def get_24h_trading_stats(self, token: str) -> Dict:
        """Get 24h trading statistics"""
        try:
            query = f"""
            {{
                tokenDayDatas(where: {{ token: "{token.lower()}" }}, orderBy: date, orderDirection: desc, first: 1) {{
                    dailyVolumeUSD
                    priceUSD
                }}
            }}
            """
            
            async with self.session.post(
                self.graph,
                json={"query": query}
            ) as resp:
                data = await resp.json()
                if data.get("data", {}).get("tokenDayDatas"):
                    stats = data["data"]["tokenDayDatas"][0]
                    return {
                        "volume_24h": float(stats.get("dailyVolumeUSD", 0)),
                        "price": float(stats.get("priceUSD", 0))
                    }
                return {}
        except Exception as e:
            logger.error(f"Aerodome stats error: {e}")
            return {}


async def create_aerodome_provider(emrd_token: Optional[str] = None) -> AerodomeProvider:
    """Factory function to create and initialize Aerodome provider"""
    provider = AerodomeProvider(emrd_token=emrd_token)
    await provider.init()
    return provider
