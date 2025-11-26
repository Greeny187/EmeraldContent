"""Learning Bot - Mini-App"""

import json
import logging
import os
from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import CommandHandler, Application

logger = logging.getLogger(__name__)

MINIAPP_URL = os.getenv(
    "MINIAPP_URL",
    "https://greeny187.github.io/EmeraldContentBots/miniapp/applearning.html"
)


async def cmd_open_learning(update, context):
    """Open Learning Mini-App"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📖 Academy öffnen",
            web_app=WebAppInfo(url=MINIAPP_URL)
        )]
    ])
    await update.message.reply_text(
        "📚 **Emerald Academy Mini-App**",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


def register_miniapp(app: Application):
    """Register miniapp commands"""
    app.add_handler(CommandHandler("learning", cmd_open_learning))
    app.add_handler(CommandHandler("academy", cmd_open_learning))


async def register_miniapp_routes(webapp: web.Application, app: Application):
    """Register HTTP routes"""
    from . import database
    
    @webapp.post("/api/learning/courses")
    async def api_courses(request):
        try:
            data = await request.json()
            user_id = data.get("user_id")
            courses = database.get_user_courses(user_id) if user_id else []
            return web.json_response({
                "status": "ok",
                "courses": [dict(c) for c in courses] if courses else []
            })
        except Exception as e:
            logger.error(f"Courses API error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    @webapp.post("/api/learning/quiz")
    async def api_quiz(request):
        try:
            data = await request.json()
            action = data.get("action")
            
            if action == "submit":
                # Process quiz answer
                pass
            
            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.error(f"Quiz API error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    @webapp.post("/api/learning/certificate")
    async def api_certificate(request):
        try:
            data = await request.json()
            user_id = data.get("user_id")
            course_id = data.get("course_id")
            
            cert_hash = database.issue_certificate(user_id, course_id)
            return web.json_response({
                "status": "ok" if cert_hash else "error",
                "certificate_hash": cert_hash
            })
        except Exception as e:
            logger.error(f"Certificate API error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    logger.info("Learning miniapp routes registered")
