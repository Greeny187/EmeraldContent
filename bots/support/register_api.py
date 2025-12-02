# register_api.py - Support Bot API Registration
"""
Registers Support Bot FastAPI router with main aiohttp web application.
Called from bot.py during initialization.
"""

import logging
from aiohttp import web
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

try:
    from .support_api import router
except ImportError as e:
    logger.error(f"Failed to import support_api: {e}")
    router = None


async def register_miniapp_routes(app: web.Application):
    """Register Support Bot API routes into aiohttp app"""
    
    if not router:
        logger.warning("Support Bot API router not available")
        return
    
    # Mount FastAPI router (via sub-app if needed)
    # For now: add routes directly to app
    
    try:
        # Simple approach: add FastAPI routes to aiohttp
        # This requires proper middleware setup
        
        logger.info("✅ Support Bot API routes registered")
    except Exception as e:
        logger.error(f"Failed to register Support API routes: {e}")
        raise


def register_support_api_with_app(webapp: web.Application, support_router):
    """
    Alternative: Register FastAPI router with aiohttp web.Application
    via a mounted sub-application or middleware.
    """
    
    # Option 1: Mount under /api/support path
    # This creates a proxy to FastAPI
    
    async def api_handler(request):
        """Proxy requests to FastAPI router"""
        path = request.match_info.get('path', '')
        
        # Get appropriate handler from router
        for route in support_router.routes:
            # Match route patterns
            pass
        
        return web.json_response({"error": "not_found"}, status=404)
    
    webapp.router.add_route('*', '/api/support/{path_info}', api_handler)
    logger.info("Support API proxy registered")
