"""Trade API Bot - Database Operations with User Management"""

import psycopg2
from psycopg2.extras import RealDictCursor
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict

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
    """Initialize all Trade API database schemas"""
    conn = get_db_connection()
    if not conn:
        logger.error("Cannot initialize schemas: No database connection")
        return
    
    try:
        cur = conn.cursor()
        
        # ===== USER SETTINGS =====
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_user_settings (
                telegram_id BIGINT PRIMARY KEY,
                theme VARCHAR(20) DEFAULT 'dark',
                notifications_enabled BOOLEAN DEFAULT TRUE,
                alert_threshold_usd NUMERIC(18,2) DEFAULT 100.00,
                preferred_currency VARCHAR(10) DEFAULT 'USD',
                language VARCHAR(10) DEFAULT 'de',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ===== API KEYS (encrypted) =====
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_keys (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                provider VARCHAR(50) NOT NULL,
                label VARCHAR(255),
                api_fields_enc TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS tradeapi_keys_tid_idx ON tradeapi_keys(telegram_id)")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_tradeapi_keys_unique ON tradeapi_keys(telegram_id, provider, COALESCE(label, ''))""")
        
        # ===== PORTFOLIOS =====
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_portfolios (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                total_value NUMERIC(18,8) DEFAULT 0,
                cash NUMERIC(18,8) DEFAULT 0,
                risk_level VARCHAR(20) DEFAULT 'medium',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(telegram_id, name)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS tradeapi_portfolios_tid_idx ON tradeapi_portfolios(telegram_id)")
        
        # ===== PORTFOLIO POSITIONS =====
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_positions (
                id BIGSERIAL PRIMARY KEY,
                portfolio_id BIGINT NOT NULL REFERENCES tradeapi_portfolios(id) ON DELETE CASCADE,
                asset_symbol VARCHAR(50),
                quantity NUMERIC(18,8),
                entry_price NUMERIC(18,8),
                current_price NUMERIC(18,8),
                cost_basis NUMERIC(18,8),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS tradeapi_positions_pid_idx ON tradeapi_positions(portfolio_id)")
        
        # ===== TRADING SIGNALS =====
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_signals (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                symbol VARCHAR(50),
                signal_type VARCHAR(20),
                confidence NUMERIC(3,2),
                strength NUMERIC(3,2),
                atr_value NUMERIC(18,8),
                entry_price NUMERIC(18,8),
                stop_loss NUMERIC(18,8),
                take_profit NUMERIC(18,8),
                position_size NUMERIC(18,8),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS tradeapi_signals_tid_idx ON tradeapi_signals(telegram_id)")
        
        # ===== TRADING ALERTS =====
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_alerts (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                symbol VARCHAR(50),
                alert_type VARCHAR(20),
                target_price NUMERIC(18,8),
                comparison VARCHAR(20),
                is_active BOOLEAN DEFAULT TRUE,
                is_triggered BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                triggered_at TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS tradeapi_alerts_tid_idx ON tradeapi_alerts(telegram_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS tradeapi_alerts_active_idx ON tradeapi_alerts(is_active) WHERE is_active = TRUE")
        
        # ===== TRADING HISTORY =====
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_trades (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                portfolio_id BIGINT REFERENCES tradeapi_portfolios(id),
                symbol VARCHAR(50),
                side VARCHAR(10),
                quantity NUMERIC(18,8),
                entry_price NUMERIC(18,8),
                exit_price NUMERIC(18,8),
                commission NUMERIC(18,8),
                pnl NUMERIC(18,8),
                pnl_percent NUMERIC(5,2),
                status VARCHAR(20),
                opened_at TIMESTAMP,
                closed_at TIMESTAMP,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS tradeapi_trades_tid_idx ON tradeapi_trades(telegram_id)")
        
        # ===== SENTIMENT CACHE =====
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_sentiment (
                id BIGSERIAL PRIMARY KEY,
                text TEXT,
                sentiment VARCHAR(20),
                positive NUMERIC(3,2),
                neutral NUMERIC(3,2),
                negative NUMERIC(3,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS tradeapi_sentiment_created_idx ON tradeapi_sentiment(created_at)")
        
        # ===== MARKET DATA CACHE =====
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_market_cache (
                id BIGSERIAL PRIMARY KEY,
                symbol VARCHAR(50),
                provider VARCHAR(50),
                price NUMERIC(18,8),
                volume NUMERIC(18,8),
                change_24h NUMERIC(5,2),
                high_24h NUMERIC(18,8),
                low_24h NUMERIC(18,8),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, provider)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS tradeapi_market_symbol_idx ON tradeapi_market_cache(symbol)")
        
        # ===== PROOFS (ON-CHAIN) =====
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradeapi_proofs (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                provider VARCHAR(50),
                symbol VARCHAR(50),
                proof_data JSONB,
                proof_hash VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS tradeapi_proofs_tid_idx ON tradeapi_proofs(telegram_id)")
        
        conn.commit()
        logger.info("Trade API schemas initialized successfully")
        
    except Exception as e:
        logger.error(f"Error initializing schemas: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()


# ===== USER SETTINGS FUNCTIONS =====
def get_user_settings(telegram_id: int) -> Dict:
    """Get user settings with defaults"""
    conn = get_db_connection()
    if not conn:
        return {}
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM tradeapi_user_settings WHERE telegram_id = %s", (telegram_id,))
        result = cur.fetchone()
        return dict(result) if result else {}
    except Exception as e:
        logger.error(f"Error fetching user settings: {e}")
        return {}
    finally:
        cur.close()
        conn.close()


def upsert_user_settings(telegram_id: int, **settings) -> bool:
    """Insert or update user settings"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cols = ', '.join(settings.keys())
        vals = ', '.join(['%s'] * len(settings))
        updates = ', '.join([f"{k}=EXCLUDED.{k}" for k in settings.keys()])
        
        query = f"""
            INSERT INTO tradeapi_user_settings (telegram_id, {cols}, updated_at)
            VALUES (%s, {vals}, NOW())
            ON CONFLICT(telegram_id) DO UPDATE SET
            {updates}, updated_at=NOW()
        """
        
        params = [telegram_id] + list(settings.values())
        cur.execute(query, params)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error upserting user settings: {e}")
        return False
    finally:
        cur.close()
        conn.close()


# ===== PORTFOLIO FUNCTIONS =====
def create_portfolio(telegram_id: int, name: str, description: str = "", risk_level: str = "medium", initial_cash: float = 10000.0) -> Optional[int]:
    """Create new portfolio"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tradeapi_portfolios (telegram_id, name, description, cash, total_value, risk_level)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (telegram_id, name, description, initial_cash, initial_cash, risk_level))
        result = cur.fetchone()
        conn.commit()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Error creating portfolio: {e}")
        return None
    finally:
        cur.close()
        conn.close()


def get_portfolios(telegram_id: int) -> List[Dict]:
    """Get all portfolios for user"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM tradeapi_portfolios
            WHERE telegram_id = %s
            ORDER BY created_at DESC
        """, (telegram_id,))
        return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching portfolios: {e}")
        return []
    finally:
        cur.close()
        conn.close()


def get_portfolio(portfolio_id: int) -> Optional[Dict]:
    """Get single portfolio"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM tradeapi_portfolios WHERE id = %s", (portfolio_id,))
        result = cur.fetchone()
        return dict(result) if result else None
    except Exception as e:
        logger.error(f"Error fetching portfolio: {e}")
        return None
    finally:
        cur.close()
        conn.close()


# ===== POSITION FUNCTIONS =====
def add_position(portfolio_id: int, symbol: str, quantity: float, entry_price: float) -> bool:
    """Add position to portfolio"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cost_basis = quantity * entry_price
        cur.execute("""
            INSERT INTO tradeapi_positions
            (portfolio_id, asset_symbol, quantity, entry_price, current_price, cost_basis)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (portfolio_id, symbol, quantity, entry_price, entry_price, cost_basis))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error adding position: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def get_positions(portfolio_id: int) -> List[Dict]:
    """Get all positions in portfolio"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM tradeapi_positions
            WHERE portfolio_id = %s
            ORDER BY created_at DESC
        """, (portfolio_id,))
        return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching positions: {e}")
        return []
    finally:
        cur.close()
        conn.close()


# ===== ALERT FUNCTIONS =====
def create_alert(telegram_id: int, symbol: str, alert_type: str, target_price: float, comparison: str = "above") -> bool:
    """Create price alert"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tradeapi_alerts
            (telegram_id, symbol, alert_type, target_price, comparison, is_active)
            VALUES (%s, %s, %s, %s, %s, TRUE)
        """, (telegram_id, symbol, alert_type, target_price, comparison))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error creating alert: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def get_alerts(telegram_id: int, active_only: bool = False) -> List[Dict]:
    """Get user's alerts"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        query = "SELECT * FROM tradeapi_alerts WHERE telegram_id = %s"
        params = [telegram_id]
        
        if active_only:
            query += " AND is_active = TRUE"
        
        query += " ORDER BY created_at DESC"
        
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching alerts: {e}")
        return []
    finally:
        cur.close()
        conn.close()


def delete_alert(alert_id: int, telegram_id: int) -> bool:
    """Delete alert"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM tradeapi_alerts WHERE id = %s AND telegram_id = %s", (alert_id, telegram_id))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.error(f"Error deleting alert: {e}")
        return False
    finally:
        cur.close()
        conn.close()


# ===== SIGNAL FUNCTIONS =====
def save_signal(telegram_id: int, symbol: str, signal_type: str, confidence: float, strength: float = 0.5, 
                atr_value: float = 0, entry_price: float = 0, stop_loss: float = 0, 
                take_profit: float = 0, position_size: float = 0) -> bool:
    """Save trading signal"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tradeapi_signals
            (telegram_id, symbol, signal_type, confidence, strength, atr_value, entry_price, stop_loss, take_profit, position_size)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (telegram_id, symbol, signal_type, confidence, strength, atr_value, entry_price, stop_loss, take_profit, position_size))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error saving signal: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def get_signals(telegram_id: int, limit: int = 50) -> List[Dict]:
    """Get recent signals"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM tradeapi_signals
            WHERE telegram_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (telegram_id, limit))
        return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching signals: {e}")
        return []
    finally:
        cur.close()
        conn.close()


# ===== TRADE FUNCTIONS =====
def record_trade(telegram_id: int, portfolio_id: int, symbol: str, side: str, quantity: float, 
                 entry_price: float, exit_price: float = 0, commission: float = 0, notes: str = "") -> bool:
    """Record a trade"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        pnl = (exit_price - entry_price) * quantity if exit_price > 0 else None
        pnl_percent = (pnl / (entry_price * quantity) * 100) if pnl and entry_price > 0 else None
        
        cur.execute("""
            INSERT INTO tradeapi_trades
            (telegram_id, portfolio_id, symbol, side, quantity, entry_price, exit_price, commission, pnl, pnl_percent, status, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (telegram_id, portfolio_id, symbol, side, quantity, entry_price, exit_price or None, commission, pnl, pnl_percent, "open" if not exit_price else "closed", notes))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error recording trade: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def get_trades(telegram_id: int, limit: int = 100) -> List[Dict]:
    """Get trading history"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM tradeapi_trades
            WHERE telegram_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (telegram_id, limit))
        return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching trades: {e}")
        return []
    finally:
        cur.close()
        conn.close()
