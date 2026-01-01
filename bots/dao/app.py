"""DAO Bot"""

from telegram.ext import Application
import logging
from datetime import datetime, timedelta

from . import handlers as cmd
from .database import init_all_schemas, close_proposal, get_active_proposals
from .miniapp import register_miniapp

logger = logging.getLogger(__name__)


def register(app: Application):
    """Register handlers"""
    cmd.register_handlers(app)


def register_jobs(app: Application):
    """Register background jobs for voting closure and treasury management"""
    
    async def check_voting_deadlines(context):
        """Check and close proposals with ended voting periods"""
        try:
            proposals = get_active_proposals()
            for proposal in proposals:
                if proposal.get('voting_end'):
                    voting_end = datetime.fromisoformat(proposal['voting_end'])
                    if datetime.now() > voting_end:
                        result = close_proposal(proposal['id'], 'closed')
                        if result:
                            logger.info(f"✅ Proposal {proposal['id']} closed")
        except Exception as e:
            logger.error(f"Voting deadline check error: {e}")
    
    # Run every 5 minutes
    if hasattr(app, 'job_queue'):
        app.job_queue.run_repeating(
            check_voting_deadlines,
            interval=300,
            first=300
        )
        logger.info("✅ DAO voting deadline job registered")


def init_schema():
    """Initialize database schema"""
    init_all_schemas()


async def register_miniapp_handler(app):
    """Register miniapp routes"""
    if hasattr(app, 'webapp'):
        await register_miniapp(app.webapp)
        logger.info("✅ DAO miniapp routes registered")
