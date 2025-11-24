"""Trade DEX Bot - Database"""

import psycopg2
from psycopg2.extras import RealDictCursor
import logging
import os

logger = logging.getLogger(__name__)

def get_db_connection():
    try:
        return psycopg2.connect(os.getenv("DATABASE_URL"))
    except Exception as e:
        logger.error(f"DB error: {e}")
        return None


def init_all_schemas():
    """Initialize DEX database schemas"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        
        # DEX Pools
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_pools (
                id SERIAL PRIMARY KEY,
                address VARCHAR(255) UNIQUE,
                token_a VARCHAR(100),
                token_b VARCHAR(100),
                liquidity_a NUMERIC(18,8),
                liquidity_b NUMERIC(18,8),
                fee_percent NUMERIC(5,2),
                tvl NUMERIC(18,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # User Pool Positions
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_positions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                pool_id INTEGER REFERENCES tradedex_pools(id),
                liquidity_share NUMERIC(18,8),
                value NUMERIC(18,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Swap History
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_swaps (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                token_from VARCHAR(100),
                token_to VARCHAR(100),
                amount_in NUMERIC(18,8),
                amount_out NUMERIC(18,8),
                price_impact NUMERIC(5,2),
                tx_hash VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Strategies
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_strategies (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                name VARCHAR(255),
                pool_id INTEGER REFERENCES tradedex_pools(id),
                strategy_type VARCHAR(50),
                config JSONB,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        logger.info("DEX schemas initialized")
    except Exception as e:
        logger.error(f"Schema error: {e}")
        conn.rollback()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_pools() -> list:
    """Get all available DEX pools"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM tradedex_pools ORDER BY tvl DESC LIMIT 50")
        return cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching pools: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_user_positions(user_id: int) -> list:
    """Get user's liquidity positions"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT p.*, po.token_a, po.token_b 
            FROM tradedex_positions p
            JOIN tradedex_pools po ON p.pool_id = po.id
            WHERE p.user_id = %s
        """, (user_id,))
        return cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching positions: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def log_swap(user_id: int, token_from: str, token_to: str, amount_in: float, amount_out: float, tx_hash: str) -> bool:
    """Log a swap transaction"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tradedex_swaps (user_id, token_from, token_to, amount_in, amount_out, tx_hash)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, token_from, token_to, amount_in, amount_out, tx_hash))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error logging swap: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
