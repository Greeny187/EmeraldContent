"""Affiliate Database - Referrals, Conversions, Commissions"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
import logging
import uuid

logger = logging.getLogger(__name__)


def get_connection():
    """Get database connection"""
    try:
        return psycopg2.connect(os.getenv("DATABASE_URL"))
    except Exception as e:
        logger.error(f"DB connection error: {e}")
        return None


def init_commission_record(referrer_id):
    """Initialize commission record for new referrer"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO aff_commissions (referrer_id, total_earned, pending, tier)
            VALUES (%s, 0, 0, 'bronze')
            ON CONFLICT (referrer_id) DO NOTHING
        """, (referrer_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Init commission error: {e}")
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def init_all_schemas():
    """Initialize all database schemas"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Referrals table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aff_referrals (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referral_id BIGINT NOT NULL,
                referral_link TEXT,
                status VARCHAR(50) DEFAULT 'pending',
                conversion_date TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(referrer_id, referral_id)
            )
        """)
        
        # Conversions table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aff_conversions (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referral_id BIGINT NOT NULL,
                conversion_type VARCHAR(50),
                value NUMERIC(20,2),
                commission NUMERIC(20,2),
                status VARCHAR(50) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW(),
                FOREIGN KEY (referrer_id) REFERENCES aff_referrals(id)
            )
        """)
        
        # Commissions table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aff_commissions (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL UNIQUE,
                total_earned NUMERIC(20,2) DEFAULT 0,
                total_withdrawn NUMERIC(20,2) DEFAULT 0,
                pending NUMERIC(20,2) DEFAULT 0,
                tier VARCHAR(50) DEFAULT 'bronze',
                wallet_address VARCHAR(255),
                ton_connect_verified BOOLEAN DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Payouts table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aff_payouts (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                amount NUMERIC(20,2),
                status VARCHAR(50) DEFAULT 'pending',
                tx_hash VARCHAR(255),
                wallet_address VARCHAR(255),
                requested_at TIMESTAMP DEFAULT NOW(),
                completed_at TIMESTAMP,
                FOREIGN KEY (referrer_id) REFERENCES aff_commissions(referrer_id)
            )
        """)
        
        conn.commit()
        logger.info("Affiliate schemas initialized")
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


def create_referral(referrer_id, referral_id):
    """Create referral"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Ensure commission record exists for referrer
        init_commission_record(referrer_id)
        
        cur.execute("""
            INSERT INTO aff_referrals (referrer_id, referral_id, status)
            VALUES (%s, %s, 'active')
            ON CONFLICT DO NOTHING
        """, (referrer_id, referral_id))
        
        conn.commit()
        logger.info(f"Referral created: {referral_id} -> {referrer_id}")
        return True
    except Exception as e:
        logger.error(f"Create referral error: {e}")
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def record_conversion(referrer_id, referral_id, conversion_type, value):
    """Record conversion event and credit commission"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Get tier info for referrer to calculate correct commission
        cur.execute("""
            SELECT total_earned FROM aff_commissions WHERE referrer_id = %s
        """, (referrer_id,))
        
        row = cur.fetchone()
        total_earned = row[0] if row else 0
        
        # Calculate commission based on tier
        if total_earned >= 10000:
            commission_rate = 0.20  # Platinum
        elif total_earned >= 5000:
            commission_rate = 0.15  # Gold
        elif total_earned >= 1000:
            commission_rate = 0.10  # Silver
        else:
            commission_rate = 0.05  # Bronze
        
        commission = value * commission_rate
        
        # Insert conversion
        cur.execute("""
            INSERT INTO aff_conversions
            (referrer_id, referral_id, conversion_type, value, commission)
            VALUES (%s, %s, %s, %s, %s)
        """, (referrer_id, referral_id, conversion_type, value, commission))
        
        # Ensure commission record exists
        init_commission_record(referrer_id)
        
        # Update commission total
        cur.execute("""
            UPDATE aff_commissions SET
            total_earned = total_earned + %s,
            pending = pending + %s,
            tier = CASE 
                WHEN total_earned + %s >= 10000 THEN 'platinum'
                WHEN total_earned + %s >= 5000 THEN 'gold'
                WHEN total_earned + %s >= 1000 THEN 'silver'
                ELSE 'bronze'
            END
            WHERE referrer_id = %s
        """, (commission, commission, commission, commission, commission, referrer_id))
        
        conn.commit()
        logger.info(f"Conversion recorded: {referrer_id} earned {commission} EMRD")
        return True
    except Exception as e:
        logger.error(f"Record conversion error: {e}")
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_referral_stats(referrer_id):
    """Get referrer stats"""
    conn = get_connection()
    if not conn:
        return {}
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT
            COUNT(DISTINCT referral_id) as total_referrals,
            COUNT(CASE WHEN status = 'active' THEN 1 END) as active_referrals,
            COALESCE(SUM(commission), 0) as total_commissions
            FROM aff_referrals
            WHERE referrer_id = %s AND status = 'active'
        """, (referrer_id,))
        
        row = cur.fetchone()
        
        cur.execute("""
            SELECT total_earned, pending, tier
            FROM aff_commissions WHERE referrer_id = %s
        """, (referrer_id,))
        
        comm_row = cur.fetchone()
        
        return {
            'total_referrals': row[0] if row else 0,
            'active_referrals': row[1] if row else 0,
            'total_commissions': float(row[2]) if row else 0,
            'total_earned': float(comm_row[0]) if comm_row else 0,
            'pending': float(comm_row[1]) if comm_row else 0,
            'tier': comm_row[2] if comm_row else 'bronze'
        }
    except Exception as e:
        logger.error(f"Get stats error: {e}")
        return {}
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def request_payout(referrer_id, amount):
    """Request payout"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Check pending balance
        cur.execute("""
            SELECT pending FROM aff_commissions WHERE referrer_id = %s
        """, (referrer_id,))
        
        row = cur.fetchone()
        if not row or row[0] < amount:
            return False
        
        # Create payout request
        cur.execute("""
            INSERT INTO aff_payouts (referrer_id, amount, status)
            VALUES (%s, %s, 'pending')
        """, (referrer_id, amount))
        
        # Update pending
        cur.execute("""
            UPDATE aff_commissions SET
            pending = pending - %s
            WHERE referrer_id = %s
        """, (amount, referrer_id))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Request payout error: {e}")
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_pending_payouts(referrer_id):
    """Get pending payouts"""
    conn = get_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT id, amount, status, requested_at
            FROM aff_payouts
            WHERE referrer_id = %s AND status = 'pending'
            ORDER BY requested_at DESC
        """, (referrer_id,))
        
        rows = cur.fetchall()
        return [
            {
                'id': row[0],
                'amount': float(row[1]),
                'status': row[2],
                'requested_at': row[3].isoformat()
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Get payouts error: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def verify_ton_wallet(referrer_id, wallet_address):
    """Verify TON wallet"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE aff_commissions SET
            wallet_address = %s,
            ton_connect_verified = TRUE
            WHERE referrer_id = %s
        """, (wallet_address, referrer_id))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Verify wallet error: {e}")
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_tier_info(referrer_id):
    """Get tier information"""
    conn = get_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT 
            total_earned,
            CASE 
                WHEN total_earned >= 10000 THEN 'platinum'
                WHEN total_earned >= 5000 THEN 'gold'
                WHEN total_earned >= 1000 THEN 'silver'
                ELSE 'bronze'
            END as tier,
            CASE 
                WHEN total_earned >= 10000 THEN '🔶'
                WHEN total_earned >= 5000 THEN '🥇'
                WHEN total_earned >= 1000 THEN '🥈'
                ELSE '🥉'
            END as tier_emoji,
            CASE 
                WHEN total_earned >= 10000 THEN 0.20
                WHEN total_earned >= 5000 THEN 0.15
                WHEN total_earned >= 1000 THEN 0.10
                ELSE 0.05
            END as commission_rate
            FROM aff_commissions
            WHERE referrer_id = %s
        """, (referrer_id,))
        
        row = cur.fetchone()
        if row:
            return {
                'total_earned': float(row[0]),
                'tier': row[1],
                'tier_emoji': row[2],
                'commission_rate': float(row[3])
            }
        return None
    except Exception as e:
        logger.error(f"Get tier error: {e}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def complete_payout(payout_id, tx_hash):
    """Complete payout with transaction hash"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE aff_payouts SET
            status = 'completed',
            tx_hash = %s,
            completed_at = NOW()
            WHERE id = %s
        """, (tx_hash, payout_id))
        
        # Get referrer_id and amount
        cur.execute("""
            SELECT referrer_id, amount FROM aff_payouts WHERE id = %s
        """, (payout_id,))
        
        row = cur.fetchone()
        if row:
            referrer_id, amount = row
            # Update total_withdrawn
            cur.execute("""
                UPDATE aff_commissions SET
                total_withdrawn = total_withdrawn + %s
                WHERE referrer_id = %s
            """, (amount, referrer_id))
        
        conn.commit()
        logger.info(f"Payout completed: {payout_id} with tx {tx_hash}")
        return True
    except Exception as e:
        logger.error(f"Complete payout error: {e}")
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_referral_link(referrer_id):
    """Get or create referral link"""
    conn = get_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT referral_link FROM aff_referrals
            WHERE referrer_id = %s LIMIT 1
        """, (referrer_id,))
        
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
        
        # Generate new link
        link = f"https://t.me/emerald_bot?start=aff_{referrer_id}"
        return link
    except Exception as e:
        logger.error(f"Get referral link error: {e}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_all_referrals(referrer_id):
    """Get all referrals with details"""
    conn = get_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT 
                referral_id,
                status,
                created_at,
                COALESCE((
                    SELECT SUM(commission) FROM aff_conversions 
                    WHERE referrer_id = aff_referrals.referrer_id 
                    AND referral_id = aff_referrals.referral_id
                ), 0) as earned
            FROM aff_referrals
            WHERE referrer_id = %s
            ORDER BY created_at DESC
        """, (referrer_id,))
        
        rows = cur.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Get all referrals error: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_payout_history(referrer_id, limit=20):
    """Get payout history"""
    conn = get_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT id, amount, status, tx_hash, requested_at, completed_at
            FROM aff_payouts
            WHERE referrer_id = %s
            ORDER BY requested_at DESC
            LIMIT %s
        """, (referrer_id, limit))
        
        rows = cur.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Get payout history error: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def calculate_payout_fee(amount):
    """Calculate payout fee (1% of amount)"""
    return amount * 0.01


def get_available_for_payout(referrer_id):
    """Get available amount after fees"""
    conn = get_connection()
    if not conn:
        return 0
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT pending FROM aff_commissions WHERE referrer_id = %s
        """, (referrer_id,))
        
        row = cur.fetchone()
        if row:
            return float(row[0]) - calculate_payout_fee(row[0])
        return 0
    except Exception as e:
        logger.error(f"Get available error: {e}")
        return 0
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
