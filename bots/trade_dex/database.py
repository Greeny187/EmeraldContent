"""Trade DEX Bot - Database"""

import psycopg2
from psycopg2.extras import RealDictCursor
import logging
import os
import json
from typing import Optional

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
        
        # ============ DEX Configuration ============
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_dex_config (
                id SERIAL PRIMARY KEY,
                name VARCHAR(50) UNIQUE,
                enabled BOOLEAN DEFAULT TRUE,
                chain VARCHAR(50),
                api_url VARCHAR(255),
                subgraph_url VARCHAR(255),
                config JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============ DEX Pools ============
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_pools (
                id SERIAL PRIMARY KEY,
                dex_name VARCHAR(50),
                pool_address VARCHAR(255) UNIQUE,
                token_a VARCHAR(255),
                token_b VARCHAR(255),
                symbol_a VARCHAR(20),
                symbol_b VARCHAR(20),
                reserve_a NUMERIC(25,8),
                reserve_b NUMERIC(25,8),
                tvl_usd NUMERIC(18,2),
                volume_24h NUMERIC(18,2),
                fee_percent NUMERIC(5,4),
                apr NUMERIC(8,4),
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unique_pool UNIQUE(dex_name, pool_address)
            )
        """)
        
        # ============ User Pool Positions ============
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_positions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                pool_id INTEGER REFERENCES tradedex_pools(id),
                dex_name VARCHAR(50),
                liquidity_share NUMERIC(25,8),
                token_a_amount NUMERIC(25,8),
                token_b_amount NUMERIC(25,8),
                value_usd NUMERIC(18,2),
                unclaimed_fees NUMERIC(25,8),
                entry_price NUMERIC(25,8),
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============ Swap History ============
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_swaps (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                dex_name VARCHAR(50),
                token_from VARCHAR(255),
                token_to VARCHAR(255),
                symbol_from VARCHAR(20),
                symbol_to VARCHAR(20),
                amount_in NUMERIC(25,8),
                amount_out NUMERIC(25,8),
                expected_amount_out NUMERIC(25,8),
                price_impact NUMERIC(8,4),
                slippage NUMERIC(8,4),
                fee_paid NUMERIC(25,8),
                tx_hash VARCHAR(255),
                status VARCHAR(20),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============ Wallet Balances ============
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_balances (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                token_address VARCHAR(255),
                symbol VARCHAR(20),
                amount NUMERIC(25,8),
                amount_usd NUMERIC(18,2),
                chain VARCHAR(50),
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unique_balance UNIQUE(user_id, token_address)
            )
        """)
        
        # ============ Trading Alerts ============
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_alerts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                alert_type VARCHAR(50),
                token_address VARCHAR(255),
                symbol VARCHAR(20),
                condition_type VARCHAR(50),
                condition_value NUMERIC(25,8),
                current_value NUMERIC(25,8),
                active BOOLEAN DEFAULT TRUE,
                triggered BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                triggered_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============ Trading Strategies ============
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_strategies (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                name VARCHAR(255) NOT NULL,
                strategy_type VARCHAR(50),
                dex_name VARCHAR(50),
                token_from VARCHAR(255),
                token_to VARCHAR(255),
                config JSONB,
                active BOOLEAN DEFAULT TRUE,
                total_executed NUMERIC(25,8),
                total_profit_loss NUMERIC(25,8),
                start_at TIMESTAMP,
                end_at TIMESTAMP,
                last_executed TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============ Strategy Executions ============
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_strategy_executions (
                id SERIAL PRIMARY KEY,
                strategy_id INTEGER REFERENCES tradedex_strategies(id),
                user_id BIGINT NOT NULL,
                executed_amount NUMERIC(25,8),
                received_amount NUMERIC(25,8),
                price NUMERIC(25,8),
                tx_hash VARCHAR(255),
                status VARCHAR(20),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============ Token Price History ============
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_price_history (
                id SERIAL PRIMARY KEY,
                token_address VARCHAR(255),
                symbol VARCHAR(20),
                chain VARCHAR(50),
                price_usd NUMERIC(25,8),
                price_change_24h NUMERIC(8,4),
                volume_24h NUMERIC(25,2),
                market_cap NUMERIC(25,2),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============ User Settings ============
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradedex_user_settings (
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE NOT NULL,
                default_dex VARCHAR(50),
                default_slippage NUMERIC(5,2),
                default_gas_price VARCHAR(50),
                notifications_enabled BOOLEAN DEFAULT TRUE,
                alert_threshold_pct NUMERIC(5,2),
                auto_approve_swaps BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes for faster queries
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tradedex_swaps_user_id ON tradedex_swaps(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tradedex_positions_user_id ON tradedex_positions(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tradedex_balances_user_id ON tradedex_balances(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tradedex_alerts_user_id ON tradedex_alerts(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tradedex_strategies_user_id ON tradedex_strategies(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tradedex_pools_dex ON tradedex_pools(dex_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tradedex_price_history_token ON tradedex_price_history(token_address, timestamp DESC)")
        
        conn.commit()
        logger.info("DEX schemas initialized successfully")
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


# ============================================================
# NEW COMPREHENSIVE DATABASE FUNCTIONS
# ============================================================

def add_alert(user_id: int, alert_type: str, token_address: str, symbol: str, 
              condition_type: str, condition_value: float) -> Optional[int]:
    """Create a new trading alert"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tradedex_alerts 
            (user_id, alert_type, token_address, symbol, condition_type, condition_value, active)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            RETURNING id
        """, (user_id, alert_type, token_address, symbol, condition_type, condition_value))
        alert_id = cur.fetchone()[0]
        conn.commit()
        return alert_id
    except Exception as e:
        logger.error(f"Error adding alert: {e}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_user_alerts(user_id: int, active_only: bool = True) -> list:
    """Get user's alerts"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if active_only:
            cur.execute("SELECT * FROM tradedex_alerts WHERE user_id = %s AND active = TRUE", (user_id,))
        else:
            cur.execute("SELECT * FROM tradedex_alerts WHERE user_id = %s", (user_id,))
        return cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching alerts: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def delete_alert(alert_id: int) -> bool:
    """Delete an alert"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM tradedex_alerts WHERE id = %s", (alert_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error deleting alert: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def create_strategy(user_id: int, name: str, strategy_type: str, dex_name: str,
                   token_from: str, token_to: str, config: dict) -> Optional[int]:
    """Create a new trading strategy"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tradedex_strategies 
            (user_id, name, strategy_type, dex_name, token_from, token_to, config, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
            RETURNING id
        """, (user_id, name, strategy_type, dex_name, token_from, token_to, json.dumps(config)))
        strategy_id = cur.fetchone()[0]
        conn.commit()
        return strategy_id
    except Exception as e:
        logger.error(f"Error creating strategy: {e}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_user_strategies(user_id: int, active_only: bool = True) -> list:
    """Get user's strategies"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if active_only:
            cur.execute("SELECT * FROM tradedex_strategies WHERE user_id = %s AND active = TRUE", (user_id,))
        else:
            cur.execute("SELECT * FROM tradedex_strategies WHERE user_id = %s", (user_id,))
        return cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching strategies: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def update_strategy_status(strategy_id: int, active: bool) -> bool:
    """Activate/deactivate a strategy"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("UPDATE tradedex_strategies SET active = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                   (active, strategy_id))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating strategy: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def log_strategy_execution(strategy_id: int, user_id: int, amount: float, received: float, tx_hash: str) -> bool:
    """Log a strategy execution"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tradedex_strategy_executions 
            (strategy_id, user_id, executed_amount, received_amount, tx_hash, status)
            VALUES (%s, %s, %s, %s, %s, 'pending')
        """, (strategy_id, user_id, amount, received, tx_hash))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error logging strategy execution: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def save_user_balance(user_id: int, token_address: str, symbol: str, amount: float, 
                     amount_usd: float, chain: str) -> bool:
    """Save or update user token balance"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tradedex_balances (user_id, token_address, symbol, amount, amount_usd, chain, last_updated)
            VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, token_address)
            DO UPDATE SET amount = %s, amount_usd = %s, last_updated = CURRENT_TIMESTAMP
        """, (user_id, token_address, symbol, amount, amount_usd, chain, amount, amount_usd))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error saving balance: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_user_balances(user_id: int) -> list:
    """Get all user token balances"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM tradedex_balances WHERE user_id = %s", (user_id,))
        return cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching balances: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_user_swap_history(user_id: int, limit: int = 50) -> list:
    """Get user's swap history"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM tradedex_swaps 
            WHERE user_id = %s 
            ORDER BY created_at DESC 
            LIMIT %s
        """, (user_id, limit))
        return cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching swap history: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def update_user_settings(user_id: int, settings: dict) -> bool:
    """Update user DEX settings"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        # Check if settings exist
        cur.execute("SELECT id FROM tradedex_user_settings WHERE user_id = %s", (user_id,))
        exists = cur.fetchone() is not None
        
        if exists:
            # Update
            cur.execute("""
                UPDATE tradedex_user_settings 
                SET default_dex = %s, default_slippage = %s, notifications_enabled = %s, 
                    alert_threshold_pct = %s, auto_approve_swaps = %s, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = %s
            """, (
                settings.get("default_dex"), settings.get("default_slippage"),
                settings.get("notifications_enabled"), settings.get("alert_threshold_pct"),
                settings.get("auto_approve_swaps"), user_id
            ))
        else:
            # Insert
            cur.execute("""
                INSERT INTO tradedex_user_settings 
                (user_id, default_dex, default_slippage, notifications_enabled, alert_threshold_pct, auto_approve_swaps)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                user_id, settings.get("default_dex"), settings.get("default_slippage", 0.5),
                settings.get("notifications_enabled", True), settings.get("alert_threshold_pct", 5),
                settings.get("auto_approve_swaps", False)
            ))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_user_settings(user_id: int) -> Optional[dict]:
    """Get user DEX settings"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM tradedex_user_settings WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        return dict(result) if result else None
    except Exception as e:
        logger.error(f"Error fetching settings: {e}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def save_pool_data(dex_name: str, pool_address: str, token_a: str, token_b: str,
                   symbol_a: str, symbol_b: str, tvl_usd: float, volume_24h: float,
                   fee_percent: float, apr: float = 0) -> bool:
    """Save pool data"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tradedex_pools 
            (dex_name, pool_address, token_a, token_b, symbol_a, symbol_b, tvl_usd, volume_24h, fee_percent, apr)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (dex_name, pool_address)
            DO UPDATE SET tvl_usd = %s, volume_24h = %s, apr = %s, last_updated = CURRENT_TIMESTAMP
        """, (dex_name, pool_address, token_a, token_b, symbol_a, symbol_b, tvl_usd, volume_24h,
              fee_percent, apr, tvl_usd, volume_24h, apr))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error saving pool data: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_top_pools(dex_name: Optional[str] = None, limit: int = 20) -> list:
    """Get top pools by TVL"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if dex_name:
            cur.execute("""
                SELECT * FROM tradedex_pools 
                WHERE dex_name = %s
                ORDER BY tvl_usd DESC 
                LIMIT %s
            """, (dex_name, limit))
        else:
            cur.execute("""
                SELECT * FROM tradedex_pools 
                ORDER BY tvl_usd DESC 
                LIMIT %s
            """, (limit,))
        return cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching top pools: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

