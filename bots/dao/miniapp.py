"""DAO MiniApp - Voting Interface"""

import logging
from aiohttp import web
import json
from .database import (
    get_active_proposals, get_proposal_details, cast_vote, 
    get_user_voting_power, delegate_voting_power, create_proposal,
    get_vote_statistics, get_treasury_balance, get_treasury_transactions,
    update_user_voting_power, create_treasury_transaction,
    get_user_voting_power_detailed, get_user_vote, get_delegations
)

logger = logging.getLogger(__name__)


async def register_miniapp(webapp):
    """Register DAO miniapp routes"""
    
    # Proposal endpoints
    webapp.router.add_post("/api/dao/proposals", get_proposals)
    webapp.router.add_post("/api/dao/proposal", get_proposal)
    webapp.router.add_post("/api/dao/proposal/create", create_new_proposal)
    
    # Voting endpoints
    webapp.router.add_post("/api/dao/vote", submit_vote)
    webapp.router.add_post("/api/dao/vote/stats", vote_statistics)
    webapp.router.add_post("/api/dao/vote/user", user_vote)
    
    # Voting power endpoints
    webapp.router.add_post("/api/dao/voting-power", get_voting_power)
    webapp.router.add_post("/api/dao/voting-power/update", update_voting_power)
    webapp.router.add_post("/api/dao/delegate", delegate_power)
    webapp.router.add_post("/api/dao/delegations", get_user_delegations)
    
    # Treasury endpoints
    webapp.router.add_post("/api/dao/treasury/balance", treasury_balance)
    webapp.router.add_post("/api/dao/treasury/transactions", treasury_transactions)
    webapp.router.add_post("/api/dao/treasury/create-tx", create_treasury_tx)
    
    logger.info("DAO miniapp routes registered")


async def register_miniapp_routes(webapp):
    """Alias for compatibility"""
    await register_miniapp(webapp)


# ============================================================================
# PROPOSAL ENDPOINTS
# ============================================================================

async def get_proposals(request):
    """GET /api/dao/proposals - List active proposals"""
    try:
        proposals = get_active_proposals()
        
        # Enrich proposals with statistics
        enriched = []
        for prop in proposals:
            stats = get_vote_statistics(prop['id'])
            prop['stats'] = stats or {}
            enriched.append(prop)
        
        return web.json_response({
            'success': True,
            'proposals': enriched,
            'count': len(enriched)
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
        
        if not proposal_id:
            return web.json_response(
                {'success': False, 'error': 'Proposal ID required'},
                status=400
            )
        
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


async def create_new_proposal(request):
    """POST /api/dao/proposal/create - Create new proposal"""
    try:
        data = await request.json()
        proposer_id = data.get('proposer_id')
        title = data.get('title')
        description = data.get('description')
        proposal_type = data.get('type', 'governance')
        
        if not all([proposer_id, title, description]):
            return web.json_response(
                {'success': False, 'error': 'Missing required fields'},
                status=400
            )
        
        # Check minimum proposer voting power (1000 EMRD)
        voting_power = get_user_voting_power(proposer_id)
        if voting_power < 1000:
            return web.json_response({
                'success': False,
                'error': f'Minimum 1000 EMRD required to create proposals. You have: {voting_power}'
            }, status=400)
        
        proposal_id = create_proposal(proposer_id, title, description, proposal_type)
        
        if proposal_id:
            return web.json_response({
                'success': True,
                'proposal_id': proposal_id,
                'message': 'Proposal created successfully'
            })
        else:
            return web.json_response(
                {'success': False, 'error': 'Failed to create proposal'},
                status=500
            )
    except Exception as e:
        logger.error(f"Create proposal error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


# ============================================================================
# VOTING ENDPOINTS
# ============================================================================

async def submit_vote(request):
    """POST /api/dao/vote - Cast vote"""
    try:
        data = await request.json()
        proposal_id = data.get('proposal_id')
        voter_id = data.get('voter_id')
        vote_type = data.get('vote_type')  # 'for' or 'against'
        
        if not all([proposal_id, voter_id, vote_type]):
            return web.json_response(
                {'success': False, 'error': 'Missing required fields'},
                status=400
            )
        
        if vote_type not in ['for', 'against']:
            return web.json_response(
                {'success': False, 'error': 'Invalid vote type'},
                status=400
            )
        
        voting_power = get_user_voting_power(voter_id)
        
        if voting_power < 100:
            return web.json_response({
                'success': False,
                'error': f'Minimum 100 EMRD required to vote. You have: {voting_power}'
            }, status=400)
        
        success = cast_vote(proposal_id, voter_id, vote_type, voting_power)
        
        if success:
            stats = get_vote_statistics(proposal_id)
            return web.json_response({
                'success': True,
                'message': 'Vote cast successfully',
                'voting_power': voting_power,
                'stats': stats or {}
            })
        else:
            return web.json_response(
                {'success': False, 'error': 'Failed to cast vote'},
                status=500
            )
    except Exception as e:
        logger.error(f"Submit vote error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def vote_statistics(request):
    """POST /api/dao/vote/stats - Get voting statistics"""
    try:
        data = await request.json()
        proposal_id = data.get('proposal_id')
        
        if not proposal_id:
            return web.json_response(
                {'success': False, 'error': 'Proposal ID required'},
                status=400
            )
        
        stats = get_vote_statistics(proposal_id)
        
        return web.json_response({
            'success': True,
            'stats': stats or {}
        })
    except Exception as e:
        logger.error(f"Vote statistics error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def user_vote(request):
    """POST /api/dao/vote/user - Get user's vote on proposal"""
    try:
        data = await request.json()
        proposal_id = data.get('proposal_id')
        voter_id = data.get('voter_id')
        
        if not all([proposal_id, voter_id]):
            return web.json_response(
                {'success': False, 'error': 'Missing required fields'},
                status=400
            )
        
        vote = get_user_vote(proposal_id, voter_id)
        
        return web.json_response({
            'success': True,
            'vote': vote
        })
    except Exception as e:
        logger.error(f"User vote error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


# ============================================================================
# VOTING POWER ENDPOINTS
# ============================================================================

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
        
        # Try detailed first, fallback to simple
        detailed = get_user_voting_power_detailed(user_id)
        if detailed:
            return web.json_response({
                'success': True,
                'voting_power': detailed['total_power'],
                'details': detailed
            })
        else:
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


async def update_voting_power(request):
    """POST /api/dao/voting-power/update - Update user voting power"""
    try:
        data = await request.json()
        user_id = data.get('user_id')
        emrd_balance = data.get('emrd_balance', 0)
        
        if not user_id:
            return web.json_response(
                {'success': False, 'error': 'User ID required'},
                status=400
            )
        
        success = update_user_voting_power(user_id, emrd_balance)
        
        if success:
            detailed = get_user_voting_power_detailed(user_id)
            return web.json_response({
                'success': True,
                'voting_power': detailed['total_power'] if detailed else emrd_balance,
                'details': detailed
            })
        else:
            return web.json_response(
                {'success': False, 'error': 'Failed to update voting power'},
                status=500
            )
    except Exception as e:
        logger.error(f"Update voting power error: {e}")
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
        
        if delegator_id == delegate_id:
            return web.json_response(
                {'success': False, 'error': 'Cannot delegate to yourself'},
                status=400
            )
        
        success = delegate_voting_power(delegator_id, delegate_id, voting_power)
        
        if success:
            # Update both parties' voting power
            delegator_power = get_user_voting_power(delegator_id)
            delegate_power_result = get_user_voting_power(delegate_id)
            
            return web.json_response({
                'success': True,
                'message': 'Delegation successful',
                'delegator_power': delegator_power,
                'delegate_power': delegate_power_result
            })
        else:
            return web.json_response(
                {'success': False, 'error': 'Failed to delegate'},
                status=500
            )
    except Exception as e:
        logger.error(f"Delegate error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def get_user_delegations(request):
    """POST /api/dao/delegations - Get user delegations"""
    try:
        data = await request.json()
        user_id = data.get('user_id')
        
        if not user_id:
            return web.json_response(
                {'success': False, 'error': 'User ID required'},
                status=400
            )
        
        delegations = get_delegations(user_id)
        
        return web.json_response({
            'success': True,
            'delegations': delegations,
            'count': len(delegations)
        })
    except Exception as e:
        logger.error(f"Get delegations error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


# ============================================================================
# TREASURY ENDPOINTS
# ============================================================================

async def treasury_balance(request):
    """POST /api/dao/treasury/balance - Get treasury balance"""
    try:
        balance = get_treasury_balance()
        
        return web.json_response({
            'success': True,
            'balance': balance,
            'currency': 'EMRD'
        })
    except Exception as e:
        logger.error(f"Treasury balance error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def treasury_transactions(request):
    """POST /api/dao/treasury/transactions - Get treasury transactions"""
    try:
        data = await request.json()
        limit = data.get('limit', 20)
        
        transactions = get_treasury_transactions(limit=limit)
        
        return web.json_response({
            'success': True,
            'transactions': transactions,
            'count': len(transactions)
        })
    except Exception as e:
        logger.error(f"Treasury transactions error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )


async def create_treasury_tx(request):
    """POST /api/dao/treasury/create-tx - Create treasury transaction"""
    try:
        data = await request.json()
        tx_type = data.get('type')
        amount = data.get('amount')
        destination = data.get('destination')
        proposal_id = data.get('proposal_id')
        
        if not all([tx_type, amount, destination]):
            return web.json_response(
                {'success': False, 'error': 'Missing required fields'},
                status=400
            )
        
        if tx_type not in ['deposit', 'withdrawal']:
            return web.json_response(
                {'success': False, 'error': 'Invalid transaction type'},
                status=400
            )
        
        tx_id = create_treasury_transaction(tx_type, amount, destination, proposal_id)
        
        if tx_id:
            return web.json_response({
                'success': True,
                'transaction_id': tx_id,
                'message': 'Transaction created'
            })
        else:
            return web.json_response(
                {'success': False, 'error': 'Failed to create transaction'},
                status=500
            )
    except Exception as e:
        logger.error(f"Create treasury transaction error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=400
        )
