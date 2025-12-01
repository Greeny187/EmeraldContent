"""Support Bot - Database"""

import psycopg2
from psycopg2.extras import RealDictCursor
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

def get_db_connection():
    try:
        return psycopg2.connect(os.getenv("DATABASE_URL"))
    except Exception as e:
        logger.error(f"DB error: {e}")
        return None


def init_all_schemas():
    """Initialize Support database schemas"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        
        # Support Tickets
        cur.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id SERIAL PRIMARY KEY,
                ticket_id VARCHAR(50) UNIQUE,
                user_id BIGINT NOT NULL,
                category VARCHAR(100),
                status VARCHAR(50) DEFAULT 'open',
                priority VARCHAR(20) DEFAULT 'normal',
                subject VARCHAR(255),
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            )
        """)
        
        # Ticket Responses
        cur.execute("""
            CREATE TABLE IF NOT EXISTS support_responses (
                id SERIAL PRIMARY KEY,
                ticket_id VARCHAR(50) REFERENCES support_tickets(ticket_id) ON DELETE CASCADE,
                user_id BIGINT,
                message TEXT,
                is_support_staff BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Support Staff
        cur.execute("""
            CREATE TABLE IF NOT EXISTS support_staff (
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE NOT NULL,
                name VARCHAR(255),
                department VARCHAR(100),
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Ticket Assignments
        cur.execute("""
            CREATE TABLE IF NOT EXISTS support_assignments (
                id SERIAL PRIMARY KEY,
                ticket_id VARCHAR(50) REFERENCES support_tickets(ticket_id),
                staff_id BIGINT REFERENCES support_staff(user_id),
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
        """)
        
        conn.commit()
        logger.info("Support schemas initialized")
    except Exception as e:
        logger.error(f"Schema error: {e}")
        conn.rollback()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def create_ticket(user_id: int, category: str, description: str, ticket_id: str) -> bool:
    """Create new support ticket"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO support_tickets (ticket_id, user_id, category, description)
            VALUES (%s, %s, %s, %s)
        """, (ticket_id, user_id, category, description))
        conn.commit()
        logger.info(f"Ticket {ticket_id} created for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error creating ticket: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_user_tickets(user_id: int) -> list:
    """Get user's tickets"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM support_tickets WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,)
        )
        return cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching tickets: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def add_response(ticket_id: str, user_id: int, message: str, is_staff: bool = False) -> bool:
    """Add response to ticket"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO support_responses (ticket_id, user_id, message, is_support_staff)
            VALUES (%s, %s, %s, %s)
        """, (ticket_id, user_id, message, is_staff))
        
        # Update ticket timestamp
        cur.execute(
            "UPDATE support_tickets SET updated_at = CURRENT_TIMESTAMP WHERE ticket_id = %s",
            (ticket_id,)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error adding response: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def close_ticket(ticket_id: str) -> bool:
    """Close support ticket"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE support_tickets 
            SET status = 'closed', resolved_at = CURRENT_TIMESTAMP
            WHERE ticket_id = %s
        """, (ticket_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error closing ticket: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def assign_ticket(ticket_id: str, staff_id: int) -> bool:
    """Assign ticket to support staff"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO support_assignments (ticket_id, staff_id)
            VALUES (%s, %s)
        """, (ticket_id, staff_id))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error assigning ticket: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
