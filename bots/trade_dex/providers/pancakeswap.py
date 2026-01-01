"""PancakeSwap DEX Integration"""

import logging
import httpx
import json
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
import asyncio

logger = logging.getLogger(__name__)

# PancakeSwap API endpoints
PANCAKE_API_V2 = "https://api.pancakeswap.info/api/v2"
PANCAKE_SUBGRAPH = "https://api.thegraph.com/subgraphs/name/pancakeswap/exchange-v2"
PANCAKE_ROUTER = "0x10ED43C718714eb2666A8B8038274567e06Cdc76"  # BSC Mainnet
PANCAKE_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"

# Token addresses on BSC
WBNB = "0xbb4CdB9CBd36B01bD1cbaebF2De08d9173bc095c"
BUSD = "0xe9e7cea3dedca5984780bafc599bd69add087d56"
USDT = "0x55d398326f99059fF775485246999027B3197955"
EMRD = "0x0000000000000000000000000000000000000000"  # Will be set from config


class PancakeSwapProvider:
    """PancakeSwap DEX Provider"""
    
    def __init__(self, bsc_rpc: str = "https://bsc-dataseed.binance.org", 
                 emrd_token: Optional[str] = None):
        self.api_url = PANCAKE_API_V2
        self.subgraph = PANCAKE_SUBGRAPH
        self.bsc_rpc = bsc_rpc
        self.router = PANCAKE_ROUTER
        self.factory = PANCAKE_FACTORY
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
    
    async def get_pool_data(self) -> List[Dict]:
        """Get all liquidity pools with stats"""
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
                }
            }
            """
            
            async with self.session.post(
                self.subgraph,
                json={"query": query}
            ) as resp:
                data = await resp.json()
                if "data" in data and "pairs" in data["data"]:
                    return data["data"]["pairs"]
                return []
        except Exception as e:
            logger.error(f"PancakeSwap pool data error: {e}")
            return []
    
    async def get_pool_by_tokens(self, token_a: str, token_b: str) -> Optional[Dict]:
        """Get specific pool data by token addresses"""
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
                }}
            }}
            """
            
            async with self.session.post(
                self.subgraph,
                json={"query": query}
            ) as resp:
                data = await resp.json()
                if data.get("data", {}).get("pairs"):
                    return data["data"]["pairs"][0]
                return None
        except Exception as e:
            logger.error(f"PancakeSwap get_pool_by_tokens error: {e}")
            return None
    
    async def get_price(self, token: str) -> Optional[float]:
        """Get token price in BUSD"""
        try:
            resp = await self.session.get(f"{self.api_url}/tokens/{token.lower()}")
            data = await resp.json()
            
            if "data" in data and "price" in data["data"]:
                return float(data["data"]["price"])
            return None
        except Exception as e:
            logger.error(f"PancakeSwap price fetch error: {e}")
            return None
    
    async def get_prices_batch(self, tokens: List[str]) -> Dict[str, float]:
        """Get prices for multiple tokens"""
        prices = {}
        tasks = [self.get_price(token) for token in tokens]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for token, price in zip(tokens, results):
            if isinstance(price, float):
                prices[token.lower()] = price
        
        return prices
    
    async def calculate_swap_amount_out(
        self, 
        token_in: str, 
        token_out: str, 
        amount_in: Decimal,
        slippage: float = 0.5
    ) -> Dict:
        """Calculate output amount for swap with price impact"""
        try:
            # Get pool data
            pool = await self.get_pool_by_tokens(token_in, token_out)
            if not pool:
                return {"error": "Pool not found"}
            
            # Simple constant product formula (k = x*y)
            reserve_in = Decimal(pool["reserve0"])
            reserve_out = Decimal(pool["reserve1"])
            
            # Account for 0.25% fee
            amount_in_with_fee = amount_in * Decimal("0.9975")
            
            # Calculate output
            amount_out = (reserve_out * amount_in_with_fee) / (reserve_in + amount_in_with_fee)
            
            # Calculate price impact
            price_impact = (amount_in / (reserve_in + amount_in)) * 100
            
            # Calculate minimum with slippage
            min_amount = amount_out * (Decimal(100 - slippage) / Decimal(100))
            
            return {
                "amount_out": str(amount_out),
                "min_amount_out": str(min_amount),
                "price_impact": float(price_impact),
                "slippage": slippage,
                "fee": str(amount_in * Decimal("0.0025"))
            }
        except Exception as e:
            logger.error(f"PancakeSwap calculate_swap error: {e}")
            return {"error": str(e)}
    
    async def get_trading_volume_24h(self, token: str) -> Optional[float]:
        """Get 24h trading volume"""
        try:
            resp = await self.session.get(f"{self.api_url}/tokens/{token.lower()}")
            data = await resp.json()
            
            if "data" in data and "volumeUSD" in data["data"]:
                return float(data["data"]["volumeUSD"])
            return None
        except Exception as e:
            logger.error(f"PancakeSwap volume error: {e}")
            return None
    
    async def get_liquidity_pools_for_token(self, token: str) -> List[Dict]:
        """Get all pools containing a specific token"""
        try:
            query = f"""
            {{
                pairs(where: {{
                    or: [
                        {{ token0: "{token.lower()}" }},
                        {{ token1: "{token.lower()}" }}
                    ]
                }}, 
                orderBy: reserveUSD, 
                orderDirection: desc,
                first: 50) {{
                    id
                    token0 {{ id symbol decimals }}
                    token1 {{ id symbol decimals }}
                    reserve0
                    reserve1
                    reserveUSD
                    volumeUSD
                    feesUSD
                }}
            }}
            """
            
            async with self.session.post(
                self.subgraph,
                json={"query": query}
            ) as resp:
                data = await resp.json()
                if "data" in data and "pairs" in data["data"]:
                    return data["data"]["pairs"]
                return []
        except Exception as e:
            logger.error(f"PancakeSwap liquidity pools error: {e}")
            return []


async def create_pancakeswap_provider(emrd_token: Optional[str] = None) -> PancakeSwapProvider:
    """Factory function to create and initialize PancakeSwap provider"""
    provider = PancakeSwapProvider(emrd_token=emrd_token)
    await provider.init()
    return provider
