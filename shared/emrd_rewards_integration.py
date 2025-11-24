# emrd_rewards_integration.py
"""
Integration Module zwischen Content Bot Handlers und EMRD Reward System
Verknüpft Bot-Events mit dem neuen Reward-System
"""

import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# Import das neue Reward System
from shared.emrd_rewards import (
    add_reward_to_queue,
    get_pending_rewards,
    create_reward_claim,
    get_user_reward_history,
    points_to_emrd_nanoton,
    emrd_nanoton_to_readable
)

# ============================================================================
# REWARD EVENTS - Punkte Vergabe nach bestimmten Aktionen
# ============================================================================

# Standard Point Values pro Event-Typ
REWARD_POINTS = {
    # Basis-Events
    "message_sent": 10,              # Nachricht schreiben
    "reaction_added": 5,             # Reaktion hinzufügen
    "user_joined": 50,               # Neuer User tritt Gruppe bei
    "daily_activity": 30,            # Täglich aktiv sein
    "weekly_streak": 100,            # Wöchentliche Aktivitäts-Serie
    
    # Content-Beitrag
    "post_created": 100,             # RSS/Content Post
    "comment_added": 20,             # Kommentar zu Post
    "post_liked": 8,                 # Like/Upvote
    
    # Engagement
    "referred_user": 200,            # User durch Referral eingebunden
    "premium_activated": 500,        # Premium aktiviert
    "feedback_submitted": 50,        # Feedback gegeben
    
    # Moderation
    "spam_reported": 15,             # Spam gemeldet
    "user_helped": 30,               # Anderen User geholfen
    
    # Spezial
    "achievement_unlocked": 250,     # Achievement erreicht
    "milestone_reached": 500,        # Milestone erreicht
}


def award_points(
    user_id: int,
    chat_id: int,
    event_type: str,
    multiplier: float = 1.0,
    custom_points: Optional[int] = None,
    metadata: Optional[Dict] = None
) -> bool:
    """
    Vergebe Punkte basierend auf Event-Typ
    
    user_id: Telegram User ID
    chat_id: Telegram Chat ID
    event_type: Art des Events (muss in REWARD_POINTS sein)
    multiplier: Punkt-Multiplikator (z.B. 1.5 für 50% Extra)
    custom_points: Überschreibe default points
    metadata: Zusätzliche Daten (werden in payload gespeichert)
    
    return: True wenn erfolgreich
    """
    try:
        # Bestimme Punkte
        if custom_points is not None:
            points = custom_points
        elif event_type not in REWARD_POINTS:
            logger.warning(f"Unknown reward event type: {event_type}")
            return False
        else:
            points = REWARD_POINTS[event_type]
        
        # Wende Multiplikator an
        points = int(points * multiplier)
        
        # Baue Payload
        payload = {
            "event_type": event_type,
            "multiplier": multiplier,
            **(metadata or {})
        }
        
        # Füge zu Reward Queue hinzu
        result = add_reward_to_queue(
            user_id=user_id,
            chat_id=chat_id,
            points=points,
            event_type=event_type,
            payload=payload
        )
        
        if result:
            emrd = emrd_nanoton_to_readable(points_to_emrd_nanoton(points))
            logger.info(f"Awarded {points} points ({emrd} EMRD) to user {user_id}: {event_type}")
        
        return result
    except Exception as e:
        logger.error(f"Error awarding points: {e}")
        return False


# ============================================================================
# TELEGRAM BOT HANDLER WRAPPERS
# ============================================================================

async def handle_user_message(user_id: int, chat_id: int, message_length: int = 0):
    """
    Callback wenn User eine Nachricht schreibt
    """
    # Basis-Punkte für Nachricht
    points = REWARD_POINTS["message_sent"]
    
    # Bonus für längere Nachrichten (100+ Zeichen = 1.2x)
    if message_length > 100:
        multiplier = 1.2
    else:
        multiplier = 1.0
    
    award_points(
        user_id=user_id,
        chat_id=chat_id,
        event_type="message_sent",
        multiplier=multiplier,
        metadata={"message_length": message_length}
    )


async def handle_user_reaction(user_id: int, chat_id: int, emoji: str):
    """
    Callback wenn User eine Reaktion hinzufügt
    """
    award_points(
        user_id=user_id,
        chat_id=chat_id,
        event_type="reaction_added",
        metadata={"emoji": emoji}
    )


async def handle_user_joined(user_id: int, chat_id: int):
    """
    Callback wenn neuer User Gruppe beitritt
    """
    award_points(
        user_id=user_id,
        chat_id=chat_id,
        event_type="user_joined"
    )


async def handle_post_created(user_id: int, chat_id: int, post_id: int, post_type: str = "rss"):
    """
    Callback wenn User Post erstellt
    """
    award_points(
        user_id=user_id,
        chat_id=chat_id,
        event_type="post_created",
        metadata={"post_id": post_id, "post_type": post_type}
    )


async def handle_premium_purchased(user_id: int, chat_id: int, plan: str):
    """
    Callback wenn User Premium aktiviert
    Bonus!
    """
    award_points(
        user_id=user_id,
        chat_id=chat_id,
        event_type="premium_activated",
        multiplier=2.0,  # Doppelte Punkte
        metadata={"plan": plan}
    )


async def handle_achievement_unlocked(user_id: int, chat_id: int, achievement: str):
    """
    Callback wenn User Achievement freischaltet
    """
    award_points(
        user_id=user_id,
        chat_id=chat_id,
        event_type="achievement_unlocked",
        metadata={"achievement": achievement}
    )


# ============================================================================
# REWARD DASHBOARD / USER FACING FUNCTIONS
# ============================================================================

async def get_user_rewards_info(user_id: int) -> Dict:
    """
    Gibt vollständige Reward-Info für einen User
    
    return: {
        "pending": {...},
        "history": [...],
        "status": "no_rewards|claimable|processing|claimed"
    }
    """
    try:
        pending = get_pending_rewards(user_id)
        history = get_user_reward_history(user_id, limit=20)
        
        # Bestimme Status
        if pending["total_points"] == 0 and not history:
            status = "no_rewards"
        elif pending["total_points"] > 0:
            status = "claimable"
        elif history:
            latest_claim = history[0]
            if latest_claim["status"] == "submitted":
                status = "processing"
            else:
                status = "claimed"
        else:
            status = "no_rewards"
        
        return {
            "status": status,
            "pending": pending,
            "history": history,
            "message": _get_status_message(status, pending)
        }
    except Exception as e:
        logger.error(f"Error getting user rewards info: {e}")
        return {"status": "error", "message": str(e)}


def _get_status_message(status: str, pending: Dict) -> str:
    """Benutzerfreundliche Nachricht basierend auf Status"""
    messages = {
        "no_rewards": "🎯 Du hast noch keine Rewards. Beginne, aktiv zu sein!",
        "claimable": f"🎉 Du hast {pending['total_emrd_readable']} EMRD zum Claimen!",
        "processing": "⏳ Deine Rewards werden gerade übertragen...",
        "claimed": "✅ Du hast Rewards erhalten!",
        "error": "❌ Fehler beim Laden deiner Rewards"
    }
    return messages.get(status, "Status unbekannt")


async def claim_user_rewards(user_id: int, wallet_address: str) -> Dict:
    """
    User kann seine ausstehenden Rewards claimen
    """
    try:
        result = await create_reward_claim(
            user_id=user_id,
            wallet_address=wallet_address,
            claim_type="manual"
        )
        return result
    except Exception as e:
        logger.error(f"Error claiming rewards: {e}")
        return {
            "success": False,
            "message": f"Fehler beim Claimen: {str(e)}"
        }


# ============================================================================
# LEADERBOARD & STATISTICS
# ============================================================================

async def get_top_earners(limit: int = 10) -> list:
    """
    Top Earners Leaderboard
    (basierend auf bereits geclaimten Rewards)
    """
    try:
        from bots.content.database import get_db_cursor
        cursor = get_db_cursor()
        
        cursor.execute(
            """
            SELECT user_id, SUM(amount) as total_emrd, COUNT(*) as claim_count
            FROM rewards_claims
            WHERE status IN ('submitted', 'confirmed')
            GROUP BY user_id
            ORDER BY total_emrd DESC
            LIMIT %s
            """,
            (limit,)
        )
        
        result = []
        for rank, row in enumerate(cursor.fetchall(), 1):
            user_id, total_nanoton, claim_count = row
            result.append({
                "rank": rank,
                "user_id": user_id,
                "total_emrd": emrd_nanoton_to_readable(total_nanoton),
                "claims": claim_count
            })
        
        return result
    except Exception as e:
        logger.error(f"Error getting leaderboard: {e}")
        return []


async def get_pending_claims_queue() -> Dict:
    """
    Admin-Funktion: Zeige alle pending Claims
    """
    try:
        from bots.content.database import get_db_cursor
        cursor = get_db_cursor()
        
        cursor.execute(
            """
            SELECT id, user_id, wallet_address, amount, created_at
            FROM rewards_claims
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT 100
            """
        )
        
        pending = []
        for row in cursor.fetchall():
            claim_id, user_id, wallet, amount_nanoton, created_at = row
            pending.append({
                "claim_id": claim_id,
                "user_id": user_id,
                "wallet": wallet,
                "amount_emrd": emrd_nanoton_to_readable(amount_nanoton),
                "created_at": created_at.isoformat() if created_at else None
            })
        
        return {
            "pending_count": len(pending),
            "total_emrd_pending": emrd_nanoton_to_readable(
                sum(int(p["amount_emrd"]) * (10**9) for p in pending)
            ),
            "claims": pending
        }
    except Exception as e:
        logger.error(f"Error getting claims queue: {e}")
        return {"pending_count": 0, "claims": []}

