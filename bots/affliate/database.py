"""Affiliate Database - Referrals, Conversions, Commissions"""

import os
import psycopg2
from datetime import datetime, timedelta
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
                FOREIGN KEY (referrer_id) REFERENCES aff_referrals(referrer_id)
            )
        """)
        
        # Commissions table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aff_commissions (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                total_earned NUMERIC(20,2) DEFAULT 0,
                total_withdrawn NUMERIC(20,2) DEFAULT 0,
                pending NUMERIC(20,2) DEFAULT 0,
                tier VARCHAR(50) DEFAULT 'bronze',
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
        
        cur.execute("""
            INSERT INTO aff_referrals (referrer_id, referral_id, status)
            VALUES (%s, %s, 'active')
            ON CONFLICT DO NOTHING
        """, (referrer_id, referral_id))
        
        conn.commit()
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
    """Record conversion event"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Calculate commission (10% default)
        commission = value * 0.10
        
        cur.execute("""
            INSERT INTO aff_conversions
            (referrer_id, referral_id, conversion_type, value, commission)
            VALUES (%s, %s, %s, %s, %s)
        """, (referrer_id, referral_id, conversion_type, value, commission))
        
        # Update commission total
        cur.execute("""
            UPDATE aff_commissions SET
            total_earned = total_earned + %s,
            pending = pending + %s
            WHERE referrer_id = %s
        """, (commission, commission, referrer_id))
        
        conn.commit()
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
