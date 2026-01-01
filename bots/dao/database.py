"""DAO Database - Proposals, Voting, Treasury"""

import os
import json
import psycopg2
from datetime import datetime, timedelta
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


def get_connection():
    """Get database connection"""
    try:
        return psycopg2.connect(os.getenv("DATABASE_URL"))
    except Exception as e:
        logger.error(f"DB connection error: {e}")
        return None


def init_all_schemas():
    """Initialize all database schemas"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Proposals table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dao_proposals (
                id SERIAL PRIMARY KEY,
                proposal_id TEXT UNIQUE NOT NULL,
                proposer_id BIGINT NOT NULL,
                title VARCHAR(255) NOT NULL,
                description TEXT,
                proposal_type VARCHAR(50),
                status VARCHAR(50) DEFAULT 'active',
                voting_start TIMESTAMP DEFAULT NOW(),
                voting_end TIMESTAMP,
                min_quorum INTEGER DEFAULT 100000,
                current_votes INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Votes table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dao_votes (
                id SERIAL PRIMARY KEY,
                proposal_id TEXT NOT NULL,
                voter_id BIGINT NOT NULL,
                vote_type VARCHAR(50),
                voting_power NUMERIC(20,2),
                timestamp TIMESTAMP DEFAULT NOW(),
                FOREIGN KEY (proposal_id) REFERENCES dao_proposals(proposal_id),
                UNIQUE(proposal_id, voter_id)
            )
        """)
        
        # Delegations table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dao_delegations (
                id SERIAL PRIMARY KEY,
                delegator_id BIGINT NOT NULL,
                delegate_id BIGINT NOT NULL,
                voting_power NUMERIC(20,2),
                created_at TIMESTAMP DEFAULT NOW(),
                expires_at TIMESTAMP,
                UNIQUE(delegator_id, delegate_id)
            )
        """)
        
        # Treasury table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dao_treasury (
                id SERIAL PRIMARY KEY,
                transaction_id TEXT UNIQUE NOT NULL,
                tx_type VARCHAR(50),
                amount NUMERIC(20,2),
                destination VARCHAR(255),
                status VARCHAR(50),
                approved_votes INTEGER,
                proposal_id TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                FOREIGN KEY (proposal_id) REFERENCES dao_proposals(proposal_id)
            )
        """)
        
        # User voting power table (cached)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dao_user_voting_power (
                user_id BIGINT PRIMARY KEY,
                emrd_balance NUMERIC(20,2) DEFAULT 0,
                delegated_power NUMERIC(20,2) DEFAULT 0,
                received_delegations NUMERIC(20,2) DEFAULT 0,
                total_power NUMERIC(20,2) DEFAULT 0,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Voting statistics
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dao_vote_stats (
                proposal_id TEXT PRIMARY KEY,
                votes_for NUMERIC(20,2) DEFAULT 0,
                votes_against NUMERIC(20,2) DEFAULT 0,
                total_voters INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT NOW(),
                FOREIGN KEY (proposal_id) REFERENCES dao_proposals(proposal_id)
            )
        """)

        conn.commit()
        logger.info("DAO schemas initialized")
        return True
    except Exception as e:
        logger.error(f"Schema init error: {e}")
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def create_proposal(proposer_id, title, description, proposal_type):
    """Create new proposal"""
    conn = get_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        proposal_id = f"prop_{int(datetime.now().timestamp())}_{proposer_id}"
        
        cur.execute("""
            INSERT INTO dao_proposals 
            (proposal_id, proposer_id, title, description, proposal_type, status)
            VALUES (%s, %s, %s, %s, %s, 'active')
            RETURNING proposal_id
        """, (proposal_id, proposer_id, title, description, proposal_type))
        
        conn.commit()
        result = cur.fetchone()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Create proposal error: {e}")
        conn.rollback()
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_active_proposals():
    """Get active proposals"""
    conn = get_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT proposal_id, title, status, current_votes, voting_end
            FROM dao_proposals
            WHERE status = 'active'
            ORDER BY created_at DESC
            LIMIT 10
        """)
        
        rows = cur.fetchall()
        return [
            {
                'id': row[0],
                'title': row[1],
                'status': row[2],
                'votes': row[3],
                'voting_end': row[4].isoformat() if row[4] else None
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Get proposals error: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def cast_vote(proposal_id, voter_id, vote_type, voting_power):
    """Cast vote on proposal"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO dao_votes 
            (proposal_id, voter_id, vote_type, voting_power)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (proposal_id, voter_id) DO UPDATE SET
            vote_type = EXCLUDED.vote_type,
            voting_power = EXCLUDED.voting_power
        """, (proposal_id, voter_id, vote_type, voting_power))
        
        # Update proposal vote count
        cur.execute("""
            UPDATE dao_proposals
            SET current_votes = (SELECT COUNT(*) FROM dao_votes WHERE proposal_id = %s)
            WHERE proposal_id = %s
        """, (proposal_id, proposal_id))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Cast vote error: {e}")
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_user_voting_power(user_id):
    """Get user voting power"""
    conn = get_connection()
    if not conn:
        return 0
    
    try:
        cur = conn.cursor()
        
        # Own EMRD balance (simplified - in real app would query blockchain)
        cur.execute("""
            SELECT COUNT(*) FROM dao_delegations WHERE delegate_id = %s
        """, (user_id,))
        
        delegated = cur.fetchone()[0] * 1000  # Simplified
        return 100000 + delegated  # Base + delegated
    except Exception as e:
        logger.error(f"Get voting power error: {e}")
        return 0
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def delegate_voting_power(delegator_id, delegate_id, voting_power):
    """Delegate voting power"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO dao_delegations
            (delegator_id, delegate_id, voting_power)
            VALUES (%s, %s, %s)
            ON CONFLICT (delegator_id, delegate_id) DO UPDATE SET
            voting_power = EXCLUDED.voting_power
        """, (delegator_id, delegate_id, voting_power))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Delegate error: {e}")
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_proposal_details(proposal_id):
    """Get proposal details with vote statistics"""
    conn = get_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT p.proposal_id, p.title, p.description, p.status, p.current_votes, 
                   p.voting_end, p.proposal_type, p.proposer_id, p.created_at,
                   vs.votes_for, vs.votes_against, vs.total_voters
            FROM dao_proposals p
            LEFT JOIN dao_vote_stats vs ON p.proposal_id = vs.proposal_id
            WHERE p.proposal_id = %s
        """, (proposal_id,))
        
        row = cur.fetchone()
        if row:
            return {
                'id': row[0],
                'title': row[1],
                'description': row[2],
                'status': row[3],
                'votes': row[4],
                'voting_end': row[5].isoformat() if row[5] else None,
                'type': row[6],
                'proposer_id': row[7],
                'created_at': row[8].isoformat() if row[8] else None,
                'votes_for': float(row[9]) if row[9] else 0,
                'votes_against': float(row[10]) if row[10] else 0,
                'total_voters': row[11] or 0
            }
        return None
    except Exception as e:
        logger.error(f"Get proposal details error: {e}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_vote_statistics(proposal_id):
    """Get detailed voting statistics"""
    conn = get_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT 
                COALESCE(SUM(CASE WHEN vote_type = 'for' THEN voting_power ELSE 0 END), 0) as votes_for,
                COALESCE(SUM(CASE WHEN vote_type = 'against' THEN voting_power ELSE 0 END), 0) as votes_against,
                COUNT(DISTINCT voter_id) as total_voters,
                COALESCE(SUM(voting_power), 0) as total_voting_power
            FROM dao_votes
            WHERE proposal_id = %s
        """, (proposal_id,))
        
        row = cur.fetchone()
        if row:
            votes_for = float(row[0]) if row[0] else 0
            votes_against = float(row[1]) if row[1] else 0
            total = votes_for + votes_against
            
            return {
                'votes_for': votes_for,
                'votes_against': votes_against,
                'total_voters': row[2] or 0,
                'total_voting_power': float(row[3]) if row[3] else 0,
                'percentage_for': (votes_for / total * 100) if total > 0 else 0,
                'percentage_against': (votes_against / total * 100) if total > 0 else 0
            }
        return None
    except Exception as e:
        logger.error(f"Get vote stats error: {e}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_treasury_balance():
    """Get current treasury balance"""
    conn = get_connection()
    if not conn:
        return 0
    
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0) FROM dao_treasury
            WHERE tx_type = 'deposit' AND status = 'approved'
            UNION ALL
            SELECT -COALESCE(SUM(amount), 0) FROM dao_treasury
            WHERE tx_type = 'withdrawal' AND status = 'approved'
        """)
        
        rows = cur.fetchall()
        balance = sum(float(row[0]) for row in rows)
        return balance
    except Exception as e:
        logger.error(f"Get treasury balance error: {e}")
        return 0
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def create_treasury_transaction(tx_type, amount, destination, proposal_id=None):
    """Create treasury transaction"""
    conn = get_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        transaction_id = f"tx_{int(datetime.now().timestamp())}"
        
        cur.execute("""
            INSERT INTO dao_treasury 
            (transaction_id, tx_type, amount, destination, status, proposal_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING transaction_id
        """, (transaction_id, tx_type, amount, destination, 'pending', proposal_id))
        
        conn.commit()
        result = cur.fetchone()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Create treasury transaction error: {e}")
        conn.rollback()
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_treasury_transactions(limit=20):
    """Get treasury transactions"""
    conn = get_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT transaction_id, tx_type, amount, destination, status, created_at
            FROM dao_treasury
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))
        
        rows = cur.fetchall()
        return [
            {
                'id': row[0],
                'type': row[1],
                'amount': float(row[2]),
                'destination': row[3],
                'status': row[4],
                'created_at': row[5].isoformat() if row[5] else None
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Get treasury transactions error: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def update_user_voting_power(user_id, emrd_balance):
    """Update user voting power"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Get delegated power
        cur.execute("""
            SELECT COALESCE(SUM(voting_power), 0) FROM dao_delegations
            WHERE delegator_id = %s
        """, (user_id,))
        
        delegated = float(cur.fetchone()[0])
        
        # Get received delegations
        cur.execute("""
            SELECT COALESCE(SUM(voting_power), 0) FROM dao_delegations
            WHERE delegate_id = %s
        """, (user_id,))
        
        received = float(cur.fetchone()[0])
        
        total_power = emrd_balance + received - delegated
        
        cur.execute("""
            INSERT INTO dao_user_voting_power
            (user_id, emrd_balance, delegated_power, received_delegations, total_power, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
            emrd_balance = EXCLUDED.emrd_balance,
            delegated_power = EXCLUDED.delegated_power,
            received_delegations = EXCLUDED.received_delegations,
            total_power = EXCLUDED.total_power,
            updated_at = NOW()
        """, (user_id, emrd_balance, delegated, received, total_power))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Update voting power error: {e}")
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_user_voting_power_detailed(user_id):
    """Get detailed user voting power"""
    conn = get_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT user_id, emrd_balance, delegated_power, received_delegations, total_power, updated_at
            FROM dao_user_voting_power
            WHERE user_id = %s
        """, (user_id,))
        
        row = cur.fetchone()
        if row:
            return {
                'user_id': row[0],
                'emrd_balance': float(row[1]),
                'delegated_power': float(row[2]),
                'received_delegations': float(row[3]),
                'total_power': float(row[4]),
                'updated_at': row[5].isoformat() if row[5] else None
            }
        return None
    except Exception as e:
        logger.error(f"Get user voting power error: {e}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_user_vote(proposal_id, voter_id):
    """Get user's vote on a proposal"""
    conn = get_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT vote_type, voting_power FROM dao_votes
            WHERE proposal_id = %s AND voter_id = %s
        """, (proposal_id, voter_id))
        
        row = cur.fetchone()
        if row:
            return {'vote_type': row[0], 'voting_power': float(row[1])}
        return None
    except Exception as e:
        logger.error(f"Get user vote error: {e}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_delegations(user_id):
    """Get user's delegations"""
    conn = get_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT delegator_id, delegate_id, voting_power, created_at, expires_at
            FROM dao_delegations
            WHERE delegator_id = %s OR delegate_id = %s
            ORDER BY created_at DESC
        """, (user_id, user_id))
        
        rows = cur.fetchall()
        return [
            {
                'delegator_id': row[0],
                'delegate_id': row[1],
                'voting_power': float(row[2]),
                'created_at': row[3].isoformat() if row[3] else None,
                'expires_at': row[4].isoformat() if row[4] else None
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Get delegations error: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def close_proposal(proposal_id, status):
    """Close proposal after voting ends"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Get vote stats
        stats = get_vote_statistics(proposal_id)
        if not stats:
            return False
        
        # Determine outcome
        outcome = 'approved' if stats['votes_for'] > stats['votes_against'] else 'rejected'
        
        cur.execute("""
            UPDATE dao_proposals
            SET status = %s, updated_at = NOW()
            WHERE proposal_id = %s
        """, (outcome, proposal_id))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Close proposal error: {e}")
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

