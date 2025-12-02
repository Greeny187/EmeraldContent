"""
Story Sharing API Endpoints
Für die Emerald Miniapp
"""

import logging
from aiohttp import web
from datetime import datetime
import os

from .story_sharing import (
    create_story_share,
    track_story_click,
    record_story_conversion,
    get_share_stats,
    get_user_shares,
    get_top_shares,
    init_story_sharing_schema,
    STORY_TEMPLATES
)
from .story_card_generator import generate_share_card, generate_share_card_html
from shared.emrd_rewards_integration import award_points

logger = logging.getLogger(__name__)


async def register_story_api(webapp):
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
        
        return web.json_response({
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
    """POST /api/stories/create - Create a new story share"""
    try:
        data = await request.json()
        
        user_id = int(data.get("user_id"))
        chat_id = int(data.get("chat_id", 0))
        template = data.get("template", "group_bot")
        group_name = data.get("group_name", "Meine Gruppe")
        
        if not user_id:
            return web.json_response(
                {"success": False, "error": "user_id erforderlich"},
                status=400
            )
        
        if template not in STORY_TEMPLATES:
            return web.json_response(
                {"success": False, "error": f"Unknown template: {template}"},
                status=400
            )
        
        # Create share
        share_info = create_story_share(user_id, chat_id, template, group_name)
        
        if not share_info:
            return web.json_response(
                {"success": False, "error": "Fehler beim Erstellen des Shares"},
                status=500
            )
        
        # Generate card image
        card_image = generate_share_card(
            template,
            group_name,
            share_info["referral_link"],
            ""
        )
        
        # Award points for sharing
        try:
            reward_points = STORY_TEMPLATES[template]["reward_points"]
            award_points(
                user_id=user_id,
                chat_id=chat_id,
                event_type="story_shared",
                custom_points=reward_points,
                metadata={"template": template, "group_name": group_name}
            )
        except Exception as e:
            logger.warning(f"Could not award points: {e}")
        
        return web.json_response({
            "success": True,
            "share": share_info,
            "has_image": card_image is not None
        })
        
    except Exception as e:
        logger.error(f"Create share error: {e}")
        return web.json_response(
            {"success": False, "error": str(e)},
            status=500
        )


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
            headers={"Cache-Control": "public, max-age=3600"}
        )
        
    except Exception as e:
        logger.error(f"Get card image error: {e}")
        return web.json_response(
            {"success": False, "error": str(e)},
            status=500
        )
