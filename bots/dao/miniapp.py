"""DAO MiniApp - Voting Interface"""

import logging
from aiohttp import web
from .database import (
    get_active_proposals, get_proposal_details, cast_vote, 
    get_user_voting_power, delegate_voting_power
)

logger = logging.getLogger(__name__)


async def register_miniapp(webapp):
    """Register DAO miniapp routes"""
    webapp.router.add_post("/api/dao/proposals", get_proposals)
    webapp.router.add_post("/api/dao/proposal", get_proposal)
    webapp.router.add_post("/api/dao/vote", submit_vote)
    webapp.router.add_post("/api/dao/voting-power", get_voting_power)
    webapp.router.add_post("/api/dao/delegate", delegate_power)
    logger.info("DAO miniapp routes registered")


async def register_miniapp_routes(webapp):
    """Alias for compatibility"""
    await register_miniapp(webapp)


async def get_proposals(request):
    """GET /api/dao/proposals - List active proposals"""
    try:
        data = await request.json()
        proposals = get_active_proposals()
        
        return web.json_response({
            'success': True,
            'proposals': proposals
        })
    except Exception as e:
        logger.error(f"Get proposals error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def get_proposal(request):
    """GET /api/dao/proposal - Get proposal details"""
    try:
        data = await request.json()
        proposal_id = data.get('proposal_id')
        
        proposal = get_proposal_details(proposal_id)
        if not proposal:
            return web.json_response(
                {'success': False, 'error': 'Proposal not found'},
                status=404
            )
        
        return web.json_response({
            'success': True,
            'proposal': proposal
        })
    except Exception as e:
        logger.error(f"Get proposal error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def submit_vote(request):
    """POST /api/dao/vote - Cast vote"""
    try:
        data = await request.json()
        proposal_id = data.get('proposal_id')
        voter_id = data.get('voter_id')
        vote_type = data.get('vote_type')
        
        if not all([proposal_id, voter_id, vote_type]):
            return web.json_response(
                {'success': False, 'error': 'Missing required fields'},
                status=400
            )
        
        voting_power = get_user_voting_power(voter_id)
        
        success = cast_vote(proposal_id, voter_id, vote_type, voting_power)
        
        return web.json_response({
            'success': success,
            'voting_power': voting_power
        })
    except Exception as e:
        logger.error(f"Submit vote error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def get_voting_power(request):
    """POST /api/dao/voting-power - Get user voting power"""
    try:
        data = await request.json()
        user_id = data.get('user_id')
        
        if not user_id:
            return web.json_response(
                {'success': False, 'error': 'User ID required'},
                status=400
            )
        
        voting_power = get_user_voting_power(user_id)
        
        return web.json_response({
            'success': True,
            'voting_power': voting_power
        })
    except Exception as e:
        logger.error(f"Get voting power error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def delegate_power(request):
    """POST /api/dao/delegate - Delegate voting power"""
    try:
        data = await request.json()
        delegator_id = data.get('delegator_id')
        delegate_id = data.get('delegate_id')
        voting_power = data.get('voting_power')
        
        if not all([delegator_id, delegate_id, voting_power]):
            return web.json_response(
                {'success': False, 'error': 'Missing required fields'},
                status=400
            )
        
        success = delegate_voting_power(delegator_id, delegate_id, voting_power)
        
        return web.json_response({'success': success})
    except Exception as e:
        logger.error(f"Delegate error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )
