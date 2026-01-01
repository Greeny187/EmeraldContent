"""Trade DEX Bot - Mini-App"""

import json
import logging
import os
from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import CommandHandler, Application
from decimal import Decimal

logger = logging.getLogger(__name__)

MINIAPP_URL = os.getenv(
    "MINIAPP_URL",
    "https://greeny187.github.io/EmeraldContentBots/miniapp/apptradedex.html"
)


async def cmd_open_dex(update, context):
    """Open DEX Mini-App"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "💱 DEX öffnen",
            web_app=WebAppInfo(url=MINIAPP_URL)
        )]
    ])
    await update.message.reply_text(
        "💱 **Trade DEX Mini-App**\n\n"
        "Öffne die App für:\n"
        "• Token Swaps\n"
        "• Liquidity Pools\n"
        "• Price Tracking\n"
        "• Automated Strategies",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


def register_miniapp(app: Application):
    """Register miniapp commands"""
    app.add_handler(CommandHandler("dex", cmd_open_dex))
    app.add_handler(CommandHandler("tradedex", cmd_open_dex))


async def register_miniapp_routes(webapp: web.Application, app: Application):
    """Register HTTP routes for DEX operations"""
    from . import database
    from .exchange_service import create_exchange_service
    
    # ============ POOL ENDPOINTS ============
    
    @webapp.post("/api/tradedex/pools")
    async def api_pools(request):
        """Get available pools"""
        try:
            # Get top pools from database
            pools = database.get_top_pools(limit=20)
            
            return web.json_response({
                "status": "ok",
                "pools": [dict(p) for p in pools] if pools else []
            })
        except Exception as e:
            logger.error(f"Pool API error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    @webapp.post("/api/tradedex/pools/search")
    async def api_pools_search(request):
        """Search pools by token"""
        try:
            data = await request.json()
            token = data.get("token", "").lower()
            dex = data.get("dex")
            
            service = await create_exchange_service()
            pools = await service.get_pools_for_token(token, dex)
            await service.close()
            
            return web.json_response({
                "status": "ok",
                "pools": pools
            })
        except Exception as e:
            logger.error(f"Pool search error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    # ============ SWAP ENDPOINTS ============
    
    @webapp.post("/api/tradedex/swap/calculate")
    async def api_swap_calculate(request):
        """Calculate swap output"""
        try:
            data = await request.json()
            token_in = data.get("token_in")
            token_out = data.get("token_out")
            amount_in = Decimal(data.get("amount_in", 0))
            dex = data.get("dex", "pancakeswap")
            slippage = float(data.get("slippage", 0.5))
            
            service = await create_exchange_service()
            
            if data.get("best_route"):
                # Find best route across all DEXes
                result = await service.find_best_swap_route(token_in, token_out, amount_in, slippage)
            else:
                # Use specific DEX
                result = await service.calculate_swap(token_in, token_out, amount_in, dex, slippage)
            
            await service.close()
            
            return web.json_response({
                "status": "ok",
                "result": result
            })
        except Exception as e:
            logger.error(f"Swap calc error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    @webapp.post("/api/tradedex/swap/execute")
    async def api_swap_execute(request):
        """Execute a swap (requires wallet connection)"""
        try:
            data = await request.json()
            user_id = data.get("user_id")
            token_from = data.get("token_from")
            token_to = data.get("token_to")
            amount_in = data.get("amount_in")
            tx_hash = data.get("tx_hash")
            dex = data.get("dex", "pancakeswap")
            
            # Log swap
            if database:
                database.log_swap(user_id, token_from, token_to, amount_in, 0, tx_hash)
            
            return web.json_response({
                "status": "ok",
                "tx_hash": tx_hash,
                "message": "Swap logged successfully"
            })
        except Exception as e:
            logger.error(f"Swap exec error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    # ============ POSITION ENDPOINTS ============
    
    @webapp.post("/api/tradedex/positions")
    async def api_positions(request):
        """Get user liquidity positions"""
        try:
            data = await request.json()
            user_id = data.get("user_id")
            
            positions = database.get_user_positions(user_id) if database else []
            
            return web.json_response({
                "status": "ok",
                "positions": [dict(p) for p in positions] if positions else []
            })
        except Exception as e:
            logger.error(f"Positions API error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    # ============ MARKET DATA ENDPOINTS ============
    
    @webapp.post("/api/tradedex/price")
    async def api_price(request):
        """Get token price"""
        try:
            data = await request.json()
            token = data.get("token")
            dex = data.get("dex", "okx")
            
            service = await create_exchange_service()
            price = await service.get_token_price(token, dex)
            await service.close()
            
            return web.json_response({
                "status": "ok",
                "token": token,
                "price": float(price) if price else None,
                "dex": dex
            })
        except Exception as e:
            logger.error(f"Price error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    @webapp.post("/api/tradedex/prices")
    async def api_prices(request):
        """Get multiple token prices"""
        try:
            data = await request.json()
            tokens = data.get("tokens", [])
            
            service = await create_exchange_service()
            prices = await service.get_prices_multi(tokens)
            await service.close()
            
            return web.json_response({
                "status": "ok",
                "prices": prices
            })
        except Exception as e:
            logger.error(f"Prices error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    @webapp.post("/api/tradedex/volume")
    async def api_volume(request):
        """Get 24h trading volume"""
        try:
            data = await request.json()
            token = data.get("token")
            dex = data.get("dex")
            
            service = await create_exchange_service()
            volume = await service.get_24h_volume(token, dex)
            await service.close()
            
            return web.json_response({
                "status": "ok",
                "token": token,
                "volume_24h": float(volume) if volume else None
            })
        except Exception as e:
            logger.error(f"Volume error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    @webapp.post("/api/tradedex/market/depth")
    async def api_depth(request):
        """Get order book depth"""
        try:
            data = await request.json()
            token_pair = data.get("token_pair")
            
            service = await create_exchange_service()
            depth = await service.get_market_depth(token_pair)
            await service.close()
            
            return web.json_response({
                "status": "ok",
                "depth": depth if depth else {}
            })
        except Exception as e:
            logger.error(f"Depth error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    @webapp.post("/api/tradedex/market/candles")
    async def api_candles(request):
        """Get candlestick data"""
        try:
            data = await request.json()
            token_pair = data.get("token_pair")
            bar = data.get("bar", "1H")
            limit = data.get("limit", 100)
            
            service = await create_exchange_service()
            candles = await service.get_candlesticks(token_pair, bar, limit)
            await service.close()
            
            return web.json_response({
                "status": "ok",
                "candles": candles
            })
        except Exception as e:
            logger.error(f"Candles error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    # ============ BALANCE ENDPOINTS ============
    
    @webapp.post("/api/tradedex/balances")
    async def api_balances(request):
        """Get user token balances"""
        try:
            data = await request.json()
            user_id = data.get("user_id")
            
            balances = database.get_user_balances(user_id) if database else []
            
            return web.json_response({
                "status": "ok",
                "balances": [dict(b) for b in balances] if balances else []
            })
        except Exception as e:
            logger.error(f"Balances error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    # ============ ALERT ENDPOINTS ============
    
    @webapp.post("/api/tradedex/alerts")
    async def api_alerts(request):
        """Get user alerts"""
        try:
            data = await request.json()
            user_id = data.get("user_id")
            
            alerts = database.get_user_alerts(user_id) if database else []
            
            return web.json_response({
                "status": "ok",
                "alerts": [dict(a) for a in alerts] if alerts else []
            })
        except Exception as e:
            logger.error(f"Alerts error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    @webapp.post("/api/tradedex/alerts/create")
    async def api_alert_create(request):
        """Create a new alert"""
        try:
            data = await request.json()
            user_id = data.get("user_id")
            alert_type = data.get("alert_type")
            token_address = data.get("token_address")
            symbol = data.get("symbol")
            condition_type = data.get("condition_type")
            condition_value = float(data.get("condition_value", 0))
            
            alert_id = database.add_alert(user_id, alert_type, token_address, symbol, condition_type, condition_value) if database else None
            
            return web.json_response({
                "status": "ok",
                "alert_id": alert_id,
                "message": "Alert created successfully"
            })
        except Exception as e:
            logger.error(f"Alert create error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    @webapp.post("/api/tradedex/alerts/delete")
    async def api_alert_delete(request):
        """Delete an alert"""
        try:
            data = await request.json()
            alert_id = data.get("alert_id")
            
            success = database.delete_alert(alert_id) if database else False
            
            return web.json_response({
                "status": "ok" if success else "error",
                "message": "Alert deleted" if success else "Failed to delete alert"
            })
        except Exception as e:
            logger.error(f"Alert delete error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    # ============ STRATEGY ENDPOINTS ============
    
    @webapp.post("/api/tradedex/strategies")
    async def api_strategies(request):
        """Get user strategies"""
        try:
            data = await request.json()
            user_id = data.get("user_id")
            
            strategies = database.get_user_strategies(user_id) if database else []
            
            return web.json_response({
                "status": "ok",
                "strategies": [dict(s) for s in strategies] if strategies else []
            })
        except Exception as e:
            logger.error(f"Strategies error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    @webapp.post("/api/tradedex/strategies/create")
    async def api_strategy_create(request):
        """Create a new strategy"""
        try:
            data = await request.json()
            user_id = data.get("user_id")
            name = data.get("name")
            strategy_type = data.get("strategy_type")
            dex_name = data.get("dex_name")
            token_from = data.get("token_from")
            token_to = data.get("token_to")
            config = data.get("config", {})
            
            strategy_id = database.create_strategy(
                user_id, name, strategy_type, dex_name, token_from, token_to, config
            ) if database else None
            
            return web.json_response({
                "status": "ok",
                "strategy_id": strategy_id,
                "message": "Strategy created successfully"
            })
        except Exception as e:
            logger.error(f"Strategy create error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    @webapp.post("/api/tradedex/strategies/toggle")
    async def api_strategy_toggle(request):
        """Activate/Deactivate strategy"""
        try:
            data = await request.json()
            strategy_id = data.get("strategy_id")
            active = data.get("active", True)
            
            success = database.update_strategy_status(strategy_id, active) if database else False
            
            return web.json_response({
                "status": "ok" if success else "error",
                "message": f"Strategy {'activated' if active else 'deactivated'}"
            })
        except Exception as e:
            logger.error(f"Strategy toggle error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    # ============ SETTINGS ENDPOINTS ============
    
    @webapp.post("/api/tradedex/settings")
    async def api_settings(request):
        """Get user settings"""
        try:
            data = await request.json()
            user_id = data.get("user_id")
            
            settings = database.get_user_settings(user_id) if database else None
            
            return web.json_response({
                "status": "ok",
                "settings": dict(settings) if settings else {}
            })
        except Exception as e:
            logger.error(f"Settings error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    @webapp.post("/api/tradedex/settings/save")
    async def api_settings_save(request):
        """Save user settings"""
        try:
            data = await request.json()
            user_id = data.get("user_id")
            settings = data.get("settings", {})
            
            success = database.update_user_settings(user_id, settings) if database else False
            
            return web.json_response({
                "status": "ok" if success else "error",
                "message": "Settings saved" if success else "Failed to save settings"
            })
        except Exception as e:
            logger.error(f"Settings save error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    # ============ SWAP HISTORY ============
    
    @webapp.post("/api/tradedex/history/swaps")
    async def api_swap_history(request):
        """Get swap history"""
        try:
            data = await request.json()
            user_id = data.get("user_id")
            limit = data.get("limit", 50)
            
            swaps = database.get_user_swap_history(user_id, limit) if database else []
            
            return web.json_response({
                "status": "ok",
                "swaps": [dict(s) for s in swaps] if swaps else []
            })
        except Exception as e:
            logger.error(f"History error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    
    logger.info("DEX miniapp routes registered successfully")
