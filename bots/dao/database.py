"""DAO Database - Proposals, Voting, Treasury"""

import os
import json
import psycopg2
from datetime import datetime
import logging

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
    """Get proposal details"""
    conn = get_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT proposal_id, title, description, status, current_votes, voting_end
            FROM dao_proposals
            WHERE proposal_id = %s
        """, (proposal_id,))
        
        row = cur.fetchone()
        if row:
            return {
                'id': row[0],
                'title': row[1],
                'description': row[2],
                'status': row[3],
                'votes': row[4],
                'voting_end': row[5].isoformat() if row[5] else None
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
