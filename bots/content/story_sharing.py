"""
Emerald Story Sharing System
Users können direkt aus der Miniapp Stories teilen mit automatischem Referral-Link
"""

import logging
import os
from typing import Optional, Tuple
from datetime import datetime, timedelta
import psycopg2
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# Story Template Typen
STORY_TEMPLATES = {
    "group_bot": {
        "title": "✨ Emerald nutzen – Story teilen",
        "description": "Diese Gruppe wird mit Emerald automatisiert",
        "emoji": "🤖",
        "color": "#10C7A0",
        "reward_points": 50
    },
    "stats": {
        "title": "📊 Meine Stats teilen",
        "description": "Schau dir meine Gruppe-Statistiken an",
        "emoji": "📊",
        "color": "#0FA890",
        "reward_points": 40
    },
    "content": {
        "title": "📝 Content Bot nutzen",
        "description": "Automatische Posts mit Emerald Content",
        "emoji": "📝",
        "color": "#10C7A0",
        "reward_points": 45
    },
    "emrd_rewards": {
        "title": "💎 EMRD erhalten – Story teilen",
        "description": "Verdiene Rewards mit Emerald",
        "emoji": "💎",
        "color": "#10C7A0",
        "reward_points": 60
    },
    "affiliate": {
        "title": "🔥 Zeig deinen Gruppen-Bot",
        "description": "Meine Gruppe wird mit Emerald gemanagt",
        "emoji": "🔥",
        "color": "#22c55e",
        "reward_points": 100
    }
}


def get_connection():
    """Get database connection"""
    try:
        return psycopg2.connect(os.getenv("DATABASE_URL"))
    except Exception as e:
        logger.error(f"DB connection error: {e}")
        return None


def init_story_sharing_schema():
    """Initialize story sharing tables"""
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Story Shares tracking
        cur.execute("""
            CREATE TABLE IF NOT EXISTS story_shares (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                chat_id BIGINT,
                story_template VARCHAR(50),
                referral_link TEXT,
                shared_at TIMESTAMP DEFAULT NOW(),
                clicks INT DEFAULT 0,
                conversions INT DEFAULT 0,
                status VARCHAR(50) DEFAULT 'shared'
            )
        """)
        
        # Story Click tracking
        cur.execute("""
            CREATE TABLE IF NOT EXISTS story_clicks (
                id SERIAL PRIMARY KEY,
                share_id INT,
                visitor_id BIGINT,
                clicked_at TIMESTAMP DEFAULT NOW(),
                source VARCHAR(50),
                FOREIGN KEY (share_id) REFERENCES story_shares(id)
            )
        """)
        
        # Story Conversions (visitor joined group/bot)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS story_conversions (
                id SERIAL PRIMARY KEY,
                share_id INT,
                referrer_id BIGINT,
                visitor_id BIGINT,
                conversion_type VARCHAR(50),
                reward_earned NUMERIC(10,2),
                converted_at TIMESTAMP DEFAULT NOW(),
                FOREIGN KEY (share_id) REFERENCES story_shares(id)
            )
        """)
        
        conn.commit()
        logger.info("✅ Story sharing schema initialized")
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


def create_story_share(
    user_id: int,
    chat_id: int,
    template: str,
    group_name: str = "Meine Gruppe"
) -> Optional[dict]:
    """Create a new story share and return share info"""
    
    if template not in STORY_TEMPLATES:
        logger.error(f"Unknown template: {template}")
        return None
    
    conn = get_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        
        # Generiere Referral-Link
        # Format: t.me/emerald_bot?start=story_USERID_SHAREID
        referral_link = f"https://t.me/emerald_bot?start=story_{user_id}"
        
        cur.execute("""
            INSERT INTO story_shares 
            (user_id, chat_id, story_template, referral_link, status)
            VALUES (%s, %s, %s, %s, 'active')
            RETURNING id
        """, (user_id, chat_id, template, referral_link))
        
        share_id = cur.fetchone()[0]
        conn.commit()
        
        template_info = STORY_TEMPLATES[template]
        
        return {
            "share_id": share_id,
            "referral_link": referral_link,
            "template": template,
            "title": template_info["title"],
            "description": template_info["description"],
            "emoji": template_info["emoji"],
            "color": template_info["color"],
            "reward_points": template_info["reward_points"],
            "group_name": group_name
        }
    except Exception as e:
        logger.error(f"Create share error: {e}")
        conn.rollback()
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def track_story_click(share_id: int, visitor_id: int, source: str = "story") -> bool:
    """Track when someone clicks on a shared story"""
    
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Record click
        cur.execute("""
            INSERT INTO story_clicks (share_id, visitor_id, source)
            VALUES (%s, %s, %s)
        """, (share_id, visitor_id, source))
        
        # Increment click count in share
        cur.execute("""
            UPDATE story_shares SET clicks = clicks + 1
            WHERE id = %s
        """, (share_id,))
        
        conn.commit()
        logger.info(f"Story click tracked: share_id={share_id}, visitor={visitor_id}")
        return True
    except Exception as e:
        logger.error(f"Track click error: {e}")
        conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def record_story_conversion(
    share_id: int,
    referrer_id: int,
    visitor_id: int,
    conversion_type: str = "joined_group",
    reward_points: float = 50.0
) -> bool:
    """Record a conversion from a story share"""
    
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Record conversion
        cur.execute("""
            INSERT INTO story_conversions 
            (share_id, referrer_id, visitor_id, conversion_type, reward_earned)
            VALUES (%s, %s, %s, %s, %s)
        """, (share_id, referrer_id, visitor_id, conversion_type, reward_points))
        
        # Increment conversion count
        cur.execute("""
            UPDATE story_shares SET conversions = conversions + 1
            WHERE id = %s
        """, (share_id,))
        
        conn.commit()
        logger.info(f"Conversion recorded: share_id={share_id}, referrer={referrer_id}")
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


def get_share_stats(share_id: int) -> Optional[dict]:
    """Get statistics for a story share"""
    
    conn = get_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT 
                id, user_id, chat_id, story_template, 
                referral_link, clicks, conversions, shared_at
            FROM story_shares
            WHERE id = %s
        """, (share_id,))
        
        row = cur.fetchone()
        if not row:
            return None
        
        return {
            "share_id": row[0],
            "user_id": row[1],
            "chat_id": row[2],
            "template": row[3],
            "referral_link": row[4],
            "clicks": row[5],
            "conversions": row[6],
            "shared_at": row[7].isoformat() if row[7] else None,
            "conversion_rate": (row[6] / row[5] * 100) if row[5] > 0 else 0
        }
    except Exception as e:
        logger.error(f"Get stats error: {e}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_user_shares(user_id: int, limit: int = 10) -> list:
    """Get all story shares by a user"""
    
    conn = get_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT 
                id, story_template, referral_link, 
                clicks, conversions, shared_at
            FROM story_shares
            WHERE user_id = %s
            ORDER BY shared_at DESC
            LIMIT %s
        """, (user_id, limit))
        
        rows = cur.fetchall()
        return [
            {
                "share_id": row[0],
                "template": row[1],
                "referral_link": row[2],
                "clicks": row[3],
                "conversions": row[4],
                "shared_at": row[5].isoformat() if row[5] else None
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Get user shares error: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_top_shares(days: int = 7, limit: int = 10) -> list:
    """Get top performing shares (leaderboard)"""
    
    conn = get_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT 
                id, user_id, story_template, clicks, conversions, shared_at
            FROM story_shares
            WHERE shared_at > NOW() - INTERVAL '%s days'
            ORDER BY conversions DESC, clicks DESC
            LIMIT %s
        """, (days, limit))
        
        rows = cur.fetchall()
        return [
            {
                "share_id": row[0],
                "user_id": row[1],
                "template": row[2],
                "clicks": row[3],
                "conversions": row[4],
                "shared_at": row[5].isoformat() if row[5] else None
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Get top shares error: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
