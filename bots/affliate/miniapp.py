"""Affiliate MiniApp - Referral Dashboard"""

import logging
import os
import json
from aiohttp import web
from .database import (
    get_referral_stats, request_payout, get_pending_payouts,
    create_referral, get_tier_info, verify_ton_wallet, complete_payout,
    init_commission_record, get_referral_link, get_all_referrals,
    get_payout_history, calculate_payout_fee, get_available_for_payout
)

logger = logging.getLogger(__name__)

MINIMUM_PAYOUT = 1000
EMRD_CONTRACT = os.getenv("EMRD_CONTRACT", "EQA0rJDTy_2sS30KxQW8HO0_ERqmOGUhMWlwdL-2RpDmCrK5")


async def register_miniapp(webapp):
    """Register Affiliate miniapp routes"""
    webapp.router.add_get("/api/affiliate/stats", get_stats)
    webapp.router.add_post("/api/affiliate/stats", get_stats)
    webapp.router.add_post("/api/affiliate/payout", request_payout_route)
    webapp.router.add_post("/api/affiliate/pending", get_pending)
    webapp.router.add_post("/api/affiliate/ton-verify", verify_wallet)
    webapp.router.add_post("/api/affiliate/claim", claim_rewards)
    webapp.router.add_post("/api/affiliate/complete-payout", complete_payout_route)
    webapp.router.add_post("/api/affiliate/referrals", get_referrals)
    webapp.router.add_post("/api/affiliate/history", get_payout_history_route)
    webapp.router.add_post("/api/affiliate/link", get_referral_link_route)
    logger.info("✅ Affiliate miniapp routes registered")


async def register_miniapp_routes(webapp):
    """Alias for compatibility"""
    await register_miniapp(webapp)


async def get_stats(request):
    """GET/POST /api/affiliate/stats - Get referral stats"""
    try:
        if request.method == "POST":
            data = await request.json()
        else:
            data = dict(request.rel_url.query)
        
        referrer_id = data.get('referrer_id')
        
        if not referrer_id:
            return web.json_response(
                {'success': False, 'error': 'Referrer ID erforderlich'},
                status=400
            )
        
        referrer_id = int(referrer_id)
        
        # Ensure commission record exists
        init_commission_record(referrer_id)
        
        stats = get_referral_stats(referrer_id)
        tier_info = get_tier_info(referrer_id)
        
        return web.json_response({
            'success': True,
            'stats': stats,
            'tier': tier_info,
            'referral_link': f"https://t.me/emerald_bot?start=aff_{referrer_id}",
            'minimum_payout': MINIMUM_PAYOUT,
            'emrd_contract': EMRD_CONTRACT
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
        referrer_id = int(data.get('referrer_id'))
        amount = float(data.get('amount'))
        wallet_address = data.get('wallet_address', '')
        
        if not referrer_id or not amount:
            return web.json_response(
                {'success': False, 'error': 'Erforderliche Felder fehlen'},
                status=400
            )
        
        if amount < MINIMUM_PAYOUT:
            return web.json_response(
                {'success': False, 'error': f'Minimum {MINIMUM_PAYOUT} EMRD erforderlich'},
                status=400
            )
        
        # Verify wallet if provided
        if wallet_address:
            verify_ton_wallet(referrer_id, wallet_address)
        
        success = request_payout(referrer_id, amount)
        
        if success:
            logger.info(f"✅ Payout requested: {referrer_id} - {amount} EMRD")
        
        return web.json_response({
            'success': success,
            'message': 'Auszahlung angefordert' if success else 'Fehler bei Auszahlung',
            'payout_fee': calculate_payout_fee(amount)
        })
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
        referrer_id = int(data.get('referrer_id'))
        
        if not referrer_id:
            return web.json_response(
                {'success': False, 'error': 'Referrer ID erforderlich'},
                status=400
            )
        
        payouts = get_pending_payouts(referrer_id)
        
        return web.json_response({
            'success': True,
            'payouts': payouts,
            'total_pending': sum(p['amount'] for p in payouts)
        })
    except Exception as e:
        logger.error(f"Get pending error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def verify_wallet(request):
    """POST /api/affiliate/ton-verify - Verify TON wallet"""
    try:
        data = await request.json()
        referrer_id = int(data.get('referrer_id'))
        wallet_address = data.get('wallet_address', '')
        
        if not referrer_id or not wallet_address:
            return web.json_response(
                {'success': False, 'error': 'Wallet-Daten erforderlich'},
                status=400
            )
        
        success = verify_ton_wallet(referrer_id, wallet_address)
        
        logger.info(f"Wallet verified for {referrer_id}: {wallet_address}")
        
        return web.json_response({
            'success': success,
            'message': 'Wallet verifiziert' if success else 'Fehler',
            'wallet': wallet_address
        })
    except Exception as e:
        logger.error(f"Verify wallet error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def claim_rewards(request):
    """POST /api/affiliate/claim - Claim EMRD rewards"""
    try:
        data = await request.json()
        referrer_id = int(data.get('referrer_id'))
        tx_hash = data.get('tx_hash', '')
        
        if not referrer_id:
            return web.json_response(
                {'success': False, 'error': 'Referrer ID erforderlich'},
                status=400
            )
        
        stats = get_referral_stats(referrer_id)
        
        logger.info(f"✅ Claiming rewards for {referrer_id}: {tx_hash}")
        
        return web.json_response({
            'success': True,
            'message': 'Rewards geclaimt',
            'tx_hash': tx_hash,
            'amount_claimed': stats.get('pending', 0)
        })
    except Exception as e:
        logger.error(f"Claim rewards error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def complete_payout_route(request):
    """POST /api/affiliate/complete-payout - Complete payout with tx hash"""
    try:
        data = await request.json()
        payout_id = int(data.get('payout_id'))
        tx_hash = data.get('tx_hash', '')
        
        if not payout_id or not tx_hash:
            return web.json_response(
                {'success': False, 'error': 'Daten erforderlich'},
                status=400
            )
        
        success = complete_payout(payout_id, tx_hash)
        
        logger.info(f"✅ Payout completed: {payout_id} - {tx_hash}")
        
        return web.json_response({
            'success': success,
            'message': 'Auszahlung abgeschlossen' if success else 'Fehler',
            'tx_hash': tx_hash
        })
    except Exception as e:
        logger.error(f"Complete payout error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def get_referrals(request):
    """POST /api/affiliate/referrals - Get all referrals"""
    try:
        data = await request.json()
        referrer_id = int(data.get('referrer_id'))
        
        if not referrer_id:
            return web.json_response(
                {'success': False, 'error': 'Referrer ID erforderlich'},
                status=400
            )
        
        referrals = get_all_referrals(referrer_id)
        
        return web.json_response({
            'success': True,
            'referrals': referrals,
            'total': len(referrals),
            'active': sum(1 for r in referrals if r.get('status') == 'active')
        })
    except Exception as e:
        logger.error(f"Get referrals error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def get_payout_history_route(request):
    """POST /api/affiliate/history - Get payout history"""
    try:
        data = await request.json()
        referrer_id = int(data.get('referrer_id'))
        limit = int(data.get('limit', 20))
        
        if not referrer_id:
            return web.json_response(
                {'success': False, 'error': 'Referrer ID erforderlich'},
                status=400
            )
        
        history = get_payout_history(referrer_id, limit)
        
        return web.json_response({
            'success': True,
            'payouts': history,
            'total': len(history)
        })
    except Exception as e:
        logger.error(f"Get history error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def get_referral_link_route(request):
    """POST /api/affiliate/link - Get referral link"""
    try:
        data = await request.json()
        referrer_id = int(data.get('referrer_id'))
        
        if not referrer_id:
            return web.json_response(
                {'success': False, 'error': 'Referrer ID erforderlich'},
                status=400
            )
        
        link = get_referral_link(referrer_id)
        if not link:
            link = f"https://t.me/emerald_bot?start=aff_{referrer_id}"
        
        return web.json_response({
            'success': True,
            'referral_link': link,
            'short_link': f"aff_{referrer_id}"
        })
    except Exception as e:
        logger.error(f"Get link error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def get_stats(request):
    """GET/POST /api/affiliate/stats - Get referral stats"""
    try:
        if request.method == "POST":
            data = await request.json()
        else:
            data = dict(request.rel_url.query)
        
        referrer_id = data.get('referrer_id')
        
        if not referrer_id:
            return web.json_response(
                {'success': False, 'error': 'Referrer ID erforderlich'},
                status=400
            )
        
        referrer_id = int(referrer_id)
        
        # Ensure commission record exists
        init_commission_record(referrer_id)
        
        stats = get_referral_stats(referrer_id)
        tier_info = get_tier_info(referrer_id)
        
        return web.json_response({
            'success': True,
            'stats': stats,
            'tier': tier_info,
            'referral_link': f"https://t.me/emerald_bot?start=aff_{referrer_id}",
            'minimum_payout': MINIMUM_PAYOUT,
            'emrd_contract': EMRD_CONTRACT
        })
    except Exception as e:
        logger.error(f"Get stats error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )

