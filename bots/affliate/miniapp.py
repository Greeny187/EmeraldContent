"""Affiliate MiniApp - Referral Dashboard"""

import logging
from aiohttp import web
from .database import (
    get_referral_stats, request_payout, get_pending_payouts,
    create_referral
)

logger = logging.getLogger(__name__)


async def register_miniapp(webapp):
    """Register Affiliate miniapp routes"""
    webapp.router.add_post("/api/affiliate/stats", get_stats)
    webapp.router.add_post("/api/affiliate/payout", request_payout_route)
    webapp.router.add_post("/api/affiliate/pending", get_pending)
    logger.info("Affiliate miniapp routes registered")


async def register_miniapp_routes(webapp):
    """Alias for compatibility"""
    await register_miniapp(webapp)


async def get_stats(request):
    """POST /api/affiliate/stats - Get referral stats"""
    try:
        data = await request.json()
        referrer_id = data.get('referrer_id')
        
        if not referrer_id:
            return web.json_response(
                {'success': False, 'error': 'Referrer ID required'},
                status=400
            )
        
        stats = get_referral_stats(referrer_id)
        
        return web.json_response({
            'success': True,
            'stats': stats,
            'referral_link': f"https://t.me/emerald_bot?start=aff_{referrer_id}"
        })
    except Exception as e:
        logger.error(f"Get stats error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def request_payout_route(request):
    """POST /api/affiliate/payout - Request payout"""
    try:
        data = await request.json()
        referrer_id = data.get('referrer_id')
        amount = data.get('amount')
        
        if not referrer_id or not amount:
            return web.json_response(
                {'success': False, 'error': 'Missing required fields'},
                status=400
            )
        
        success = request_payout(referrer_id, float(amount))
        
        return web.json_response({'success': success})
    except Exception as e:
        logger.error(f"Request payout error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def get_pending(request):
    """POST /api/affiliate/pending - Get pending payouts"""
    try:
        data = await request.json()
        referrer_id = data.get('referrer_id')
        
        if not referrer_id:
            return web.json_response(
                {'success': False, 'error': 'Referrer ID required'},
                status=400
            )
        
        payouts = get_pending_payouts(referrer_id)
        
        return web.json_response({
            'success': True,
            'payouts': payouts
        })
    except Exception as e:
        logger.error(f"Get pending error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )
