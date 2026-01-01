"""Unified Exchange Service Layer"""

import logging
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from enum import Enum
import asyncio
import os

from .providers import (
    PancakeSwapProvider,
    AerodomeProvider,
    OKXProvider,
    create_pancakeswap_provider,
    create_aerodome_provider,
    create_okx_provider
)

logger = logging.getLogger(__name__)


class DEXType(Enum):
    """Supported DEX types"""
    PANCAKESWAP = "pancakeswap"
    AERODOME = "aerodome"
    STON_FI = "ston_fi"
    DEDUST = "dedust"


class ChainType(Enum):
    """Supported blockchains"""
    BSC = "bsc"
    EVMOS = "evmos"
    TON = "ton"
    ETHEREUM = "ethereum"
    ARBITRUM = "arbitrum"


class ExchangeService:
    """Unified service for all DEX and exchange operations"""
    
    def __init__(
        self,
        pancake_enabled: bool = True,
        aerodome_enabled: bool = True,
        okx_api_key: Optional[str] = None,
        okx_secret: Optional[str] = None,
        okx_passphrase: Optional[str] = None
    ):
        self.pancake = None
        self.aerodome = None
        self.okx = None
        self.pancake_enabled = pancake_enabled
        self.aerodome_enabled = aerodome_enabled
        
        # OKX credentials from env if not provided
        self.okx_api_key = okx_api_key or os.getenv("OKX_API_KEY")
        self.okx_secret = okx_secret or os.getenv("OKX_SECRET_KEY")
        self.okx_passphrase = okx_passphrase or os.getenv("OKX_PASSPHRASE")
    
    async def init(self):
        """Initialize all providers"""
        try:
            if self.pancake_enabled:
                self.pancake = await create_pancakeswap_provider()
                logger.info("PancakeSwap provider initialized")
        except Exception as e:
            logger.error(f"Failed to initialize PancakeSwap: {e}")
        
        try:
            if self.aerodome_enabled:
                self.aerodome = await create_aerodome_provider()
                logger.info("Aerodome provider initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Aerodome: {e}")
        
        try:
            if self.okx_api_key and self.okx_secret and self.okx_passphrase:
                self.okx = await create_okx_provider(
                    self.okx_api_key,
                    self.okx_secret,
                    self.okx_passphrase
                )
                logger.info("OKX provider initialized")
        except Exception as e:
            logger.error(f"Failed to initialize OKX: {e}")
    
    async def close(self):
        """Close all provider connections"""
        if self.pancake:
            await self.pancake.close()
        if self.aerodome:
            await self.aerodome.close()
        if self.okx:
            await self.okx.close()
    
    async def __aenter__(self):
        await self.init()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    # ========== Pool & Market Data ==========
    
    async def get_all_pools(self) -> Dict[str, List[Dict]]:
        """Get pools from all available DEXes"""
        pools = {}
        
        if self.pancake:
            try:
                pools["pancakeswap"] = await self.pancake.get_pool_data()
            except Exception as e:
                logger.error(f"Error fetching PancakeSwap pools: {e}")
        
        if self.aerodome:
            try:
                pools["aerodome"] = await self.aerodome.get_pools()
            except Exception as e:
                logger.error(f"Error fetching Aerodome pools: {e}")
        
        return pools
    
    async def get_pools_for_token(self, token: str, dex: Optional[str] = None) -> List[Dict]:
        """Get all pools containing a token"""
        pools = []
        
        if dex in (None, "pancakeswap") and self.pancake:
            try:
                token_pools = await self.pancake.get_liquidity_pools_for_token(token)
                pools.extend([{**p, "dex": "pancakeswap"} for p in token_pools])
            except Exception as e:
                logger.error(f"Error fetching PancakeSwap pools: {e}")
        
        if dex in (None, "aerodome") and self.aerodome:
            try:
                token_pools = await self.aerodome.get_pools()
                # Filter for pools containing the token
                filtered = [p for p in token_pools 
                          if token.lower() in [p.get("token0", {}).get("id", "").lower(),
                                              p.get("token1", {}).get("id", "").lower()]]
                pools.extend([{**p, "dex": "aerodome"} for p in filtered])
            except Exception as e:
                logger.error(f"Error fetching Aerodome pools: {e}")
        
        return pools
    
    async def get_token_price(self, token: str, dex: str = "okx") -> Optional[float]:
        """Get token price from specified DEX/Exchange"""
        try:
            if dex == "okx" and self.okx:
                # Try OKX
                ticker = await self.okx.get_ticker(token)
                if ticker:
                    return float(ticker.get("last", 0))
            
            # Fallback to others
            if self.pancake:
                price = await self.pancake.get_price(token)
                if price:
                    return price
            
            if self.aerodome:
                prices = await self.aerodome.get_prices_batch([token])
                if token.lower() in prices:
                    return prices[token.lower()]
        except Exception as e:
            logger.error(f"Error fetching price for {token}: {e}")
        
        return None
    
    async def get_prices_multi(self, tokens: List[str]) -> Dict[str, float]:
        """Get prices for multiple tokens from best available source"""
        prices = {}
        
        # Try OKX first if available
        if self.okx:
            try:
                # Map to OKX instrument IDs (e.g., BTC-USD, ETH-USD)
                results = await self.okx.get_price_24h_change([f"{t.upper()}-USDT" for t in tokens])
                for token in tokens:
                    key = f"{token.upper()}-USDT"
                    if key in results:
                        prices[token.lower()] = results[key]["last_price"]
            except Exception as e:
                logger.debug(f"OKX multi-price error: {e}")
        
        # Fill gaps from other sources
        missing = [t for t in tokens if t.lower() not in prices]
        
        if missing and self.pancake:
            try:
                pancake_prices = await self.pancake.get_prices_batch(missing)
                prices.update(pancake_prices)
            except Exception as e:
                logger.debug(f"PancakeSwap batch price error: {e}")
        
        if missing and self.aerodome:
            try:
                aerodome_prices = await self.aerodome.get_prices_batch(missing)
                prices.update(aerodome_prices)
            except Exception as e:
                logger.debug(f"Aerodome batch price error: {e}")
        
        return prices
    
    async def get_24h_volume(self, token: str, dex: Optional[str] = None) -> Optional[float]:
        """Get 24h trading volume"""
        if dex == "pancakeswap" or (not dex and self.pancake):
            try:
                return await self.pancake.get_trading_volume_24h(token)
            except Exception as e:
                logger.error(f"PancakeSwap volume error: {e}")
        
        if dex == "aerodome" or (not dex and self.aerodome):
            try:
                stats = await self.aerodome.get_24h_trading_stats(token)
                return stats.get("volume_24h")
            except Exception as e:
                logger.error(f"Aerodome volume error: {e}")
        
        if dex == "okx" or (not dex and self.okx):
            try:
                return await self.okx.get_trading_volume_24h(f"{token.upper()}-USDT")
            except Exception as e:
                logger.error(f"OKX volume error: {e}")
        
        return None
    
    # ========== Swap & Exchange Calculations ==========
    
    async def calculate_swap(
        self,
        token_in: str,
        token_out: str,
        amount_in: Decimal,
        dex: str = "pancakeswap",
        slippage: float = 0.5
    ) -> Dict:
        """Calculate swap output with price impact"""
        
        if dex == "pancakeswap" and self.pancake:
            return await self.pancake.calculate_swap_amount_out(
                token_in, token_out, amount_in, slippage
            )
        
        elif dex == "aerodome" and self.aerodome:
            return await self.aerodome.calculate_swap_output(
                token_in, token_out, amount_in, slippage
            )
        
        else:
            return {"error": f"DEX {dex} not available"}
    
    async def find_best_swap_route(
        self,
        token_in: str,
        token_out: str,
        amount_in: Decimal,
        slippage: float = 0.5
    ) -> Dict:
        """Find best swap route across all DEXes"""
        
        results = {}
        
        if self.pancake:
            try:
                result = await self.pancake.calculate_swap_amount_out(
                    token_in, token_out, amount_in, slippage
                )
                if "error" not in result:
                    results["pancakeswap"] = result
            except Exception as e:
                logger.debug(f"PancakeSwap routing error: {e}")
        
        if self.aerodome:
            try:
                result = await self.aerodome.calculate_swap_output(
                    token_in, token_out, amount_in, slippage
                )
                if "error" not in result:
                    results["aerodome"] = result
            except Exception as e:
                logger.debug(f"Aerodome routing error: {e}")
        
        # Find best route
        if results:
            best_dex = max(results.keys(), key=lambda x: Decimal(results[x].get("amount_out", 0)))
            return {
                "best_dex": best_dex,
                "amount_out": results[best_dex]["amount_out"],
                "min_amount_out": results[best_dex]["min_amount_out"],
                "price_impact": results[best_dex]["price_impact"],
                "all_routes": results
            }
        
        return {"error": "No routes available"}
    
    # ========== Market Data & Analytics ==========
    
    async def get_market_depth(self, token_pair: str) -> Optional[Dict]:
        """Get order book depth from OKX"""
        if self.okx:
            try:
                return await self.okx.get_depth(token_pair)
            except Exception as e:
                logger.error(f"Depth error: {e}")
        return None
    
    async def get_candlesticks(
        self,
        token_pair: str,
        bar: str = "1H",
        limit: int = 100
    ) -> List:
        """Get OHLC candlestick data from OKX"""
        if self.okx:
            try:
                return await self.okx.get_candlesticks(token_pair, bar, limit)
            except Exception as e:
                logger.error(f"Candlesticks error: {e}")
        return []
    
    async def get_spot_pairs(self) -> List[str]:
        """Get all available spot trading pairs"""
        pairs = []
        if self.okx:
            try:
                pairs = await self.okx.get_spot_trading_pairs()
            except Exception as e:
                logger.error(f"Spot pairs error: {e}")
        return pairs
    
    async def get_funding_rates(self, tokens: List[str]) -> Dict[str, Optional[float]]:
        """Get funding rates for perpetual contracts"""
        rates = {}
        if self.okx:
            try:
                for token in tokens:
                    inst_id = f"{token.upper()}-USDT-SWAP"
                    funding = await self.okx.get_funding_rate(inst_id)
                    if funding:
                        rates[token] = funding["funding_rate"]
            except Exception as e:
                logger.error(f"Funding rate error: {e}")
        return rates
    
    async def get_liquidity_positions(
        self,
        wallet_address: str,
        dex: Optional[str] = None
    ) -> Dict[str, List[Dict]]:
        """Get user's liquidity positions"""
        positions = {}
        
        if (dex in (None, "aerodome") and self.aerodome):
            try:
                positions["aerodome"] = await self.aerodome.get_user_liquidity_positions(
                    wallet_address
                )
            except Exception as e:
                logger.error(f"Aerodome positions error: {e}")
        
        return positions


async def create_exchange_service(
    pancake_enabled: bool = True,
    aerodome_enabled: bool = True
) -> ExchangeService:
    """Factory function for ExchangeService"""
    service = ExchangeService(
        pancake_enabled=pancake_enabled,
        aerodome_enabled=aerodome_enabled
    )
    await service.init()
    return service
