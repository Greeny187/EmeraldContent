"""
Story Sharing API Endpoints
Für die Emerald Miniapp
"""

import logging
from aiohttp import web
from datetime import datetime
import os

from .story_sharing import (
    create_story_share, track_story_click,
    record_story_conversion, get_share_stats, get_user_shares,
    get_top_shares, init_story_sharing_schema, count_shares_today, get_share_by_id,
    STORY_TEMPLATES
)
from .story_card_generator import generate_share_card, generate_share_card_html
try:
    from shared.emrd_rewards_integration import award_points
except Exception:
    def award_points(*args, **kwargs):
        return None

from .database import get_story_settings

logger = logging.getLogger(__name__)

def _allowed_origin(request: web.Request) -> str:
    try:
        return request.app.get("allowed_origin") or "*"
    except Exception:
        return "*"

def _cors_headers(request: web.Request) -> dict:
    return {
        "Access-Control-Allow-Origin": _allowed_origin(request),
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data, x-telegram-web-app-data",
    }

async def _cors_ok(request: web.Request) -> web.Response:
    return web.Response(status=204, headers=_cors_headers(request))

def _json(request: web.Request, payload: dict, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status, headers=_cors_headers(request))

# Telegram WebApp Auth (wie in miniapp.py)
def _resolve_uid(request: web.Request) -> int:
    try:
        from .access import parse_webapp_user_id
    except Exception:
        parse_webapp_user_id = None
    init_str = (
        request.headers.get("X-Telegram-Init-Data")
        or request.headers.get("x-telegram-web-app-data")
        or request.query.get("init_data")
    )
    uid = 0
    try:
        if parse_webapp_user_id:
            uid = int(parse_webapp_user_id(init_str) or 0)
    except Exception:
        uid = 0
    if uid > 0:
        return uid
    q_uid = request.query.get("uid")
    if q_uid and str(q_uid).lstrip("-").isdigit():
        return int(q_uid)
    return 0

async def _is_admin(webapp_or_app, chat_id: int, user_id: int) -> bool:
    """Server-seitiger Admincheck (wichtig, weil Story-Sharing farmbar ist)."""
    app = None
    try:
        app = webapp_or_app.get("ptb_app") if not hasattr(webapp_or_app, "bot") else webapp_or_app
    except Exception:
        app = None
    if not app:
        return False
    try:
        cm = await app.bot.get_chat_member(chat_id, user_id)
        status = (getattr(cm, "status", "") or "").lower()
        return status in ("administrator", "creator")
    except Exception:
        return False

def register_story_api(webapp: web.Application):
    """Register story sharing API routes"""
    
    # Initialize schema
    init_story_sharing_schema()
    
    webapp.router.add_get("/api/stories/templates", get_templates)
    webapp.router.add_post("/api/stories/create", create_share)
    webapp.router.add_post("/api/stories/click", click_share)
    webapp.router.add_post("/api/stories/convert", convert_share)
    webapp.router.add_get("/api/stories/stats/{share_id}", get_stats)
    webapp.router.add_get("/api/stories/user/{user_id}", get_user_stories)
    webapp.router.add_get("/api/stories/top", get_top)
    webapp.router.add_get("/api/stories/card/{template}", get_card_image)
    webapp.router.add_get("/api/stories/card/share/{share_id}", get_share_card_image)

    # CORS Preflight
    for p in (
        "/api/stories/templates",
        "/api/stories/create",
        "/api/stories/click",
        "/api/stories/convert",
        "/api/stories/top",
        "/api/stories/card/{template}",
        "/api/stories/card/share/{share_id}",
        "/api/stories/stats/{share_id}",
        "/api/stories/user/{user_id}",
    ):
        webapp.router.add_route("OPTIONS", p, _cors_ok)
        
    logger.info("✅ Story sharing API routes registered")

async def get_templates(request: web.Request) -> web.Response:
    """GET /api/stories/templates - Get available story templates"""
    try:
        templates = []
        for key, template in STORY_TEMPLATES.items():
            templates.append({
                "id": key,
                "title": template["title"],
                "description": template["description"],
                "emoji": template["emoji"],
                "color": template["color"],
                "reward_points": template["reward_points"]
            })
        
        return _json(request, {
            "success": True,
            "templates": templates
        })
    except Exception as e:
        logger.error(f"Get templates error: {e}")
        return web.json_response(
            {"success": False, "error": str(e)},
            status=500
        )

async def create_share(request: web.Request) -> web.Response:
    """POST /api/stories/create - Create a new story share (JSON)"""
    try:
        data = await request.json()
        uid = _resolve_uid(request)
        chat_id = int(data.get("chat_id", 0) or 0)
        template = (data.get("template") or "group_bot").strip()

        if uid <= 0:
            return _json(request, {"success": False, "error": "auth_required"}, status=403)
        if chat_id == 0:
            return _json(request, {"success": False, "error": "chat_id erforderlich"}, status=400)

        # Admin-Check (pro Gruppe)
        if not await _is_admin(request.app, chat_id, uid):
            return _json(request, {"success": False, "error": "forbidden"}, status=403)

        # Settings pro Gruppe
        try:
            sharing = get_story_settings(chat_id) or {}
        except Exception:
            sharing = {}

        if not bool(sharing.get("enabled", True)):
            return _json(request, {"success": False, "error": "story_sharing_disabled"}, status=403)

        templates = sharing.get("templates") or {}
        if template in templates and not bool(templates.get(template)):
            return _json(request, {"success": False, "error": "template_disabled"}, status=403)

        # Rate-Limit (pro User, pro Gruppe, pro Tag)
        try:
            limit = int(sharing.get("daily_limit", 5) or 5)
        except Exception:
            limit = 5
        used = count_shares_today(uid, chat_id)
        if used >= limit:
            return _json(request, {"success": False, "error": "rate_limited", "limit": limit, "used": used}, status=429)

        # Gruppenname (für Card) – best effort
        group_name = (data.get("group_name") or "").strip() or "Meine Gruppe"
        share = create_story_share(uid, chat_id, template, group_name=group_name)
        if not share:
            return _json(request, {"success": False, "error": "create_failed"}, status=500)

        origin = f"{request.scheme}://{request.host}"
        card_url = f"{origin}/api/stories/card/share/{share['share_id']}"

        return _json(request, {
            "success": True,
            "share": {
                "share_id": share["share_id"],
                "chat_id": chat_id,
                "template": share["template"],
                "referral_link": share["referral_link"],
                "card_url": card_url,
                "title": share.get("title"),
                "description": share.get("description"),
            }
        })
    except Exception as e:
        logger.error(f"Create share error: {e}", exc_info=True)
        return _json(request, {"success": False, "error": str(e)}, status=500)


async def get_share_card_image(request: web.Request) -> web.Response:
    """GET /api/stories/card/share/{share_id} - Card by share_id (best for Telegram Story)."""
    try:
        share_id = int(request.match_info.get("share_id", "0"))
        row = get_share_by_id(share_id)
        if not row:
            return _json(request, {"success": False, "error": "not_found"}, status=404)

        template = row.get("template") or "group_bot"
        referral_link = row.get("referral_link") or ""
        chat_id = int(row.get("chat_id") or 0)

        group_name = "Meine Gruppe"
        if chat_id:
            try:
                import psycopg2
                from .database import db_url
                conn = psycopg2.connect(db_url)
                cur = conn.cursor()
                cur.execute("SELECT title FROM group_settings WHERE chat_id=%s;", (chat_id,))
                r = cur.fetchone()
                if r and r[0]:
                    group_name = str(r[0])
                cur.close(); conn.close()
            except Exception:
                pass

        card_data = generate_share_card(template, group_name, referral_link)
        if not card_data:
            return _json(request, {"success": False, "error": "card_failed"}, status=500)

        return web.Response(
            body=card_data,
            content_type="image/png",
            headers={**_cors_headers(request), "Cache-Control": "public, max-age=3600"}
        )
    except Exception as e:
        logger.error(f"Get share card image error: {e}", exc_info=True)
        return _json(request, {"success": False, "error": str(e)}, status=500)

async def click_share(request: web.Request) -> web.Response:
    """POST /api/stories/click - Track a story click"""
    try:
        data = await request.json()
        
        share_id = int(data.get("share_id"))
        visitor_id = int(data.get("visitor_id", 0))
        source = data.get("source", "story")
        
        if not share_id:
            return web.json_response(
                {"success": False, "error": "share_id erforderlich"},
                status=400
            )
        
        # Track click
        track_story_click(share_id, visitor_id or 0, source)
        
        return web.json_response({"success": True})
        
    except Exception as e:
        logger.error(f"Click share error: {e}")
        return web.json_response(
            {"success": False, "error": str(e)},
            status=500
        )


async def convert_share(request: web.Request) -> web.Response:
    """POST /api/stories/convert - Record a conversion"""
    try:
        data = await request.json()
        
        share_id = int(data.get("share_id"))
        referrer_id = int(data.get("referrer_id"))
        visitor_id = int(data.get("visitor_id"))
        conversion_type = data.get("conversion_type", "joined_group")
        reward_points = float(data.get("reward_points", 50.0))
        
        if not all([share_id, referrer_id, visitor_id]):
            return web.json_response(
                {"success": False, "error": "Erforderliche Felder fehlen"},
                status=400
            )
        
        # Record conversion
        record_story_conversion(
            share_id,
            referrer_id,
            visitor_id,
            conversion_type,
            reward_points
        )
        
        # Award points to referrer
        try:
            award_points(
                user_id=referrer_id,
                chat_id=0,
                event_type="referred_user",
                custom_points=int(reward_points),
                metadata={"conversion_type": conversion_type, "via_story": True}
            )
        except Exception as e:
            logger.warning(f"Could not award referral points: {e}")
        
        return web.json_response({"success": True})
        
    except Exception as e:
        logger.error(f"Convert share error: {e}")
        return web.json_response(
            {"success": False, "error": str(e)},
            status=500
        )


async def get_stats(request: web.Request) -> web.Response:
    """GET /api/stories/stats/{share_id} - Get share statistics"""
    try:
        share_id = int(request.match_info.get("share_id", 0))
        
        if not share_id:
            return web.json_response(
                {"success": False, "error": "share_id erforderlich"},
                status=400
            )
        
        stats = get_share_stats(share_id)
        
        if not stats:
            return web.json_response(
                {"success": False, "error": "Share nicht gefunden"},
                status=404
            )
        
        return web.json_response({
            "success": True,
            "stats": stats
        })
        
    except Exception as e:
        logger.error(f"Get stats error: {e}")
        return web.json_response(
            {"success": False, "error": str(e)},
            status=500
        )


async def get_user_stories(request: web.Request) -> web.Response:
    """GET /api/stories/user/{user_id} - Get user's stories"""
    try:
        user_id = int(request.match_info.get("user_id", 0))
        limit = int(request.rel_url.query.get("limit", 10))
        
        if not user_id:
            return web.json_response(
                {"success": False, "error": "user_id erforderlich"},
                status=400
            )
        
        shares = get_user_shares(user_id, limit)
        
        return web.json_response({
            "success": True,
            "shares": shares,
            "count": len(shares)
        })
        
    except Exception as e:
        logger.error(f"Get user stories error: {e}")
        return web.json_response(
            {"success": False, "error": str(e)},
            status=500
        )


async def get_top(request: web.Request) -> web.Response:
    """GET /api/stories/top - Get top performing shares"""
    try:
        days = int(request.rel_url.query.get("days", 7))
        limit = int(request.rel_url.query.get("limit", 10))
        
        shares = get_top_shares(days, limit)
        
        return web.json_response({
            "success": True,
            "shares": shares,
            "period_days": days
        })
        
    except Exception as e:
        logger.error(f"Get top error: {e}")
        return web.json_response(
            {"success": False, "error": str(e)},
            status=500
        )


async def get_card_image(request: web.Request) -> web.Response:
    """GET /api/stories/card/{template} - Get share card image"""
    try:
        template = request.match_info.get("template", "group_bot")
        group_name = request.rel_url.query.get("group", "Meine Gruppe")
        referral_link = request.rel_url.query.get("link", "")
        
        # Generate card
        card_data = generate_share_card(
            template,
            group_name,
            referral_link
        )
        
        if not card_data:
            return web.json_response(
                {"success": False, "error": "Fehler beim Generieren der Card"},
                status=500
            )
        
        return web.Response(
            body=card_data,
            content_type="image/png",
            headers={**_cors_headers(request), "Cache-Control": "public, max-age=3600"}
        )
        
    except Exception as e:
        logger.error(f"Get card image error: {e}")
        return _json(request, {
            "success": False, "error": str(e)
            }, status=500)
