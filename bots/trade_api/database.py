"""Trade API Bot - Database Operations"""

import psycopg2
from psycopg2.extras import RealDictCursor
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

def get_db_connection():
    """Get database connection"""
    try:
        import os
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None


def init_all_schemas():
    """Initialize Trade API database schemas"""
    conn = get_db_connection()
    if not conn:
        logger.error("Cannot initialize schemas: No database connection")
        return
    
    try:
        cur = conn.cursor()
        
        # Portfolios table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_portfolios (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL UNIQUE,
                name VARCHAR(255),
                total_value NUMERIC(18,8),
                cash NUMERIC(18,8),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Portfolio positions
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_positions (
                id SERIAL PRIMARY KEY,
                portfolio_id INTEGER REFERENCES tradeapi_portfolios(id) ON DELETE CASCADE,
                asset_symbol VARCHAR(50),
                quantity NUMERIC(18,8),
                entry_price NUMERIC(18,8),
                current_price NUMERIC(18,8),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Trading signals
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_signals (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                symbol VARCHAR(50),
                signal_type VARCHAR(20),
                confidence NUMERIC(3,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Price alerts
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_alerts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                symbol VARCHAR(50),
                alert_type VARCHAR(20),
                target_price NUMERIC(18,8),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                triggered_at TIMESTAMP
            )
        """)
        
        # On-chain proofs
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_proofs (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                tx_hash VARCHAR(255),
                asset VARCHAR(100),
                amount NUMERIC(18,8),
                proof_type VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                verified_at TIMESTAMP
            )
        """)
        
        conn.commit()
        logger.info("Trade API schemas initialized successfully")
        
    except Exception as e:
        logger.error(f"Error initializing schemas: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()


def create_portfolio(user_id: int, name: str) -> bool:
    """Create new portfolio for user"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tradeapi_portfolios (user_id, name, total_value, cash) VALUES (%s, %s, %s, %s)",
            (user_id, name, 0, 0)
        )
        conn.commit()
        logger.info(f"Portfolio created for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error creating portfolio: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def get_portfolio(user_id: int) -> Optional[dict]:
    """Get user's portfolio"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM tradeapi_portfolios WHERE user_id = %s", (user_id,))
        return cur.fetchone()
    except Exception as e:
        logger.error(f"Error fetching portfolio: {e}")
        return None
    finally:
        cur.close()
        conn.close()


def add_position(portfolio_id: int, asset_symbol: str, quantity: float, entry_price: float) -> bool:
    """Add position to portfolio"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tradeapi_positions (portfolio_id, asset_symbol, quantity, entry_price)
            VALUES (%s, %s, %s, %s)
        """, (portfolio_id, asset_symbol, quantity, entry_price))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error adding position: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def get_positions(portfolio_id: int) -> list:
    """Get all positions in portfolio"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM tradeapi_positions WHERE portfolio_id = %s", (portfolio_id,))
        return cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching positions: {e}")
        return []
    finally:
        cur.close()
        conn.close()


def add_alert(user_id: int, symbol: str, alert_type: str, target_price: float) -> bool:
    """Add price alert"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tradeapi_alerts (user_id, symbol, alert_type, target_price)
            VALUES (%s, %s, %s, %s)
        """, (user_id, symbol, alert_type, target_price))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error adding alert: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def get_alerts(user_id: int) -> list:
    """Get user's active alerts"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM tradeapi_alerts WHERE user_id = %s AND triggered_at IS NULL",
            (user_id,)
        )
        return cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching alerts: {e}")
        return []
    finally:
        cur.close()
        conn.close()
