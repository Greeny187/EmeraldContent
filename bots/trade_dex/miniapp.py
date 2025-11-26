"""Trade DEX Bot - Mini-App"""

import json
import logging
import os
from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import CommandHandler, Application

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
        "💱 **Trade DEX Mini-App**",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


def register_miniapp(app: Application):
    """Register miniapp commands"""
    app.add_handler(CommandHandler("dex", cmd_open_dex))
    app.add_handler(CommandHandler("swap", cmd_open_dex))


async def register_miniapp_routes(webapp: web.Application, app: Application):
    """Register HTTP routes"""
    from . import database
    
    @webapp.post("/api/tradedex/pools")
    async def api_pools(request):
        try:
            pools = database.get_pools()
            return web.json_response({
                "status": "ok",
                "pools": [dict(p) for p in pools] if pools else []
            })
        except Exception as e:
            logger.error(f"Pool API error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    @webapp.post("/api/tradedex/positions")
    async def api_positions(request):
        try:
            data = await request.json()
            user_id = data.get("user_id")
            positions = database.get_user_positions(user_id)
            return web.json_response({
                "status": "ok",
                "positions": [dict(p) for p in positions] if positions else []
            })
        except Exception as e:
            logger.error(f"Positions API error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    @webapp.post("/api/tradedex/swap")
    async def api_swap(request):
        try:
            data = await request.json()
            # Process swap
            return web.json_response({
                "status": "ok",
                "tx_hash": None
            })
        except Exception as e:
            logger.error(f"Swap API error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    logger.info("DEX miniapp routes registered")
