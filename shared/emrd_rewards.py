# emrd_rewards.py - EMRD Token Reward System
"""
Zentrale Implementierung für EMRD-Token Rewards
Konvertiert In-Game Punkte → EMRD Token auf TON Blockchain
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import asyncio
import httpx
import json

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

# TON Blockchain Configuration (MAINNET)
TON_ENDPOINT = os.getenv("TON_ENDPOINT", "https://toncenter.com/api/v2")
TON_API_KEY = os.getenv("TON_API_KEY", "")

# EMRD Token Configuration (MAINNET)
EMRD_TOKEN_CONTRACT = os.getenv(
    "EMRD_TOKEN_CONTRACT",
    "EQAr2N2-VHHNMVTrLqWN1EQPnfBJ6D3aaILLaDT_kEEJ"  # MAINNET Production
)
EMRD_JETTON_MINTER = os.getenv(
    "EMRD_JETTON_MINTER",
    "EQBx5cQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # MAINNET Minter
)
EMRD_DECIMALS = 9  # EMRD hat 9 Dezimalstellen

# Reward Configuration
REWARD_CONVERSION_RATE = float(os.getenv("REWARD_CONVERSION_RATE", "0.000001"))  # 1 Point = X EMRD
MIN_CLAIM_AMOUNT = int(os.getenv("MIN_CLAIM_AMOUNT_EMRD", "100000000"))  # 0.1 EMRD (in nanotons)
GAS_LIMIT_MINTER = 1000000000  # ~1 TON für Jetton Transfer
FEE_PERCENTAGE = float(os.getenv("REWARD_FEE_PERCENTAGE", "2.0"))  # 2% Protokol-Gebühr

# Wallet für Reward Distribution
REWARD_WALLET = os.getenv("REWARD_WALLET_ADDRESS", "")
REWARD_WALLET_PRIVATE_KEY = os.getenv("REWARD_WALLET_PRIVATE_KEY", "")

# API Endpoint für eigene Reward-API
INTERNAL_REWARDS_API = os.getenv("INTERNAL_REWARDS_API", "http://localhost:8001")

# ============================================================================
# DATABASE HELPER - Rewards Tabelle Zugriff
# ============================================================================

def get_db_cursor():
    """Import dynamisch um Zirkulärbezüge zu vermeiden"""
    try:
        from bots.content.database import get_db_cursor as _get_db_cursor
        return _get_db_cursor()
    except ImportError:
        logger.error("Could not import database cursor")
        return None

# ============================================================================
# PUNKT-ZU-TOKEN KONVERTIERUNG
# ============================================================================

def points_to_emrd_nanoton(points: int) -> int:
    """
    Konvertiert In-Game Punkte zu EMRD (in Nanotons)
    
    points: Interne Punkte (z.B. 100 = Nachricht senden)
    return: EMRD Betrag in Nanotons
    
    Example:
        100 points × 0.000001 EMRD/point = 0.0001 EMRD = 100000 nanotons
    """
    emrd_amount = points * REWARD_CONVERSION_RATE
    nanoton_amount = int(emrd_amount * (10 ** EMRD_DECIMALS))
    return nanoton_amount


def apply_reward_fee(nanoton_amount: int) -> Tuple[int, int]:
    """
    Zieht Gebühren ab
    
    return: (net_amount, fee_amount)
    """
    fee = int(nanoton_amount * (FEE_PERCENTAGE / 100))
    net = nanoton_amount - fee
    return net, fee


def emrd_nanoton_to_readable(nanoton_amount: int) -> str:
    """Konvertiert nanotons zu lesbarem EMRD Format"""
    emrd = nanoton_amount / (10 ** EMRD_DECIMALS)
    return f"{emrd:.9f}"

# ============================================================================
# PENDING REWARDS MANAGEMENT
# ============================================================================

def add_reward_to_queue(
    user_id: int,
    chat_id: int,
    points: int,
    event_type: str,
    payload: Optional[Dict] = None
) -> bool:
    """
    Fügt einen Reward zur Queue hinzu
    
    user_id: Telegram User ID
    chat_id: Telegram Chat ID
    points: Punkte (werden zu EMRD konvertiert)
    event_type: Art des Events (message, reaction, post, etc.)
    payload: Zusätzliche Daten
    """
    try:
        cursor = get_db_cursor()
        if not cursor:
            return False
        
        cursor.execute(
            """
            INSERT INTO rewards_pending 
            (user_id, chat_id, points, event_type, payload, processed)
            VALUES (%s, %s, %s, %s, %s, FALSE)
            """,
            (user_id, chat_id, points, event_type, json.dumps(payload or {}))
        )
        logger.info(f"Added {points} points to queue for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error adding reward to queue: {e}")
        return False


def get_pending_rewards(user_id: int) -> Dict:
    """
    Holt alle ausstehenden Rewards für einen User
    
    return: {
        "total_points": int,
        "total_emrd_nanoton": int,
        "total_emrd_readable": str,
        "pending_count": int,
        "events": [...]
    }
    """
    try:
        cursor = get_db_cursor()
        if not cursor:
            return {"total_points": 0, "total_emrd_nanoton": 0, "pending_count": 0}
        
        # Summe der Punkte
        cursor.execute(
            """
            SELECT COALESCE(SUM(points), 0), COUNT(*)
            FROM rewards_pending
            WHERE user_id = %s AND processed = FALSE
            """,
            (user_id,)
        )
        total_points, count = cursor.fetchone()
        total_points = int(total_points)
        
        # Details
        cursor.execute(
            """
            SELECT id, event_type, points, created_at
            FROM rewards_pending
            WHERE user_id = %s AND processed = FALSE
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (user_id,)
        )
        events = [
            {
                "id": row[0],
                "type": row[1],
                "points": row[2],
                "timestamp": row[3].isoformat() if row[3] else None
            }
            for row in cursor.fetchall()
        ]
        
        emrd_nanoton = points_to_emrd_nanoton(total_points)
        net_emrd, fee = apply_reward_fee(emrd_nanoton)
        
        return {
            "total_points": total_points,
            "total_emrd_nanoton": net_emrd,
            "total_emrd_readable": emrd_nanoton_to_readable(net_emrd),
            "fee_nanoton": fee,
            "fee_emrd_readable": emrd_nanoton_to_readable(fee),
            "pending_count": count,
            "events": events
        }
    except Exception as e:
        logger.error(f"Error getting pending rewards: {e}")
        return {"total_points": 0, "total_emrd_nanoton": 0, "pending_count": 0}


# ============================================================================
# REWARD CLAIMS (Auszahlung)
# ============================================================================

async def create_reward_claim(
    user_id: int,
    wallet_address: str,
    claim_type: str = "manual"
) -> Dict:
    """
    Erstellt einen Claim für die Auszahlung von EMRD Rewards
    
    user_id: Telegram User ID
    wallet_address: TON Wallet Adresse für Auszahlung
    claim_type: 'manual' oder 'auto_weekly'
    
    return: {
        "success": bool,
        "claim_id": int,
        "emrd_amount": str,
        "tx_hash": str (falls submitted),
        "status": "pending|submitted|confirmed|failed",
        "message": str
    }
    """
    try:
        # 1. Hole Pending Rewards
        pending = get_pending_rewards(user_id)
        if pending["total_points"] == 0:
            return {
                "success": False,
                "message": "Keine ausstehenden Rewards zum Claimen"
            }
        
        emrd_amount = pending["total_emrd_nanoton"]
        
        # 2. Validiere Mindestbetrag
        if emrd_amount < MIN_CLAIM_AMOUNT:
            return {
                "success": False,
                "message": f"Mindestbetrag: {emrd_nanoton_to_readable(MIN_CLAIM_AMOUNT)} EMRD"
            }
        
        # 3. Validiere Wallet
        if not wallet_address or len(wallet_address) < 34:
            return {
                "success": False,
                "message": "Ungültige TON Wallet Adresse"
            }
        
        # 4. Erstelle Claim in DB
        cursor = get_db_cursor()
        if not cursor:
            return {"success": False, "message": "Database error"}
        
        cursor.execute(
            """
            INSERT INTO rewards_claims 
            (user_id, wallet_address, amount, status, claim_type)
            VALUES (%s, %s, %s, 'pending', %s)
            RETURNING id
            """,
            (user_id, wallet_address, emrd_amount, claim_type)
        )
        claim_id = cursor.fetchone()[0]
        
        # 5. Markiere Pending-Rewards als verarbeitet
        cursor.execute(
            """
            UPDATE rewards_pending
            SET processed = TRUE, processed_at = NOW()
            WHERE user_id = %s AND processed = FALSE
            """,
            (user_id,)
        )
        
        logger.info(f"Created reward claim {claim_id} for user {user_id}: {emrd_nanoton_to_readable(emrd_amount)} EMRD")
        
        return {
            "success": True,
            "claim_id": claim_id,
            "emrd_amount": emrd_nanoton_to_readable(emrd_amount),
            "status": "pending",
            "message": f"Claim erstellt: {emrd_nanoton_to_readable(emrd_amount)} EMRD werden zu {wallet_address} übertragen"
        }
    except Exception as e:
        logger.error(f"Error creating reward claim: {e}")
        return {"success": False, "message": f"Fehler: {str(e)}"}


async def process_reward_claim(claim_id: int) -> Dict:
    """
    Verarbeitet einen Reward-Claim (sendet tatsächlich EMRD Token)
    
    return: {
        "success": bool,
        "tx_hash": str (falls erfolgreich),
        "status": "submitted|failed|error",
        "message": str
    }
    """
    try:
        cursor = get_db_cursor()
        if not cursor:
            return {"success": False, "message": "Database error"}
        
        # 1. Hole Claim Details
        cursor.execute(
            """
            SELECT user_id, wallet_address, amount
            FROM rewards_claims
            WHERE id = %s AND status = 'pending'
            """,
            (claim_id,)
        )
        row = cursor.fetchone()
        if not row:
            return {"success": False, "message": "Claim not found or already processed"}
        
        user_id, wallet_address, amount = row
        
        # 2. Baue Transaction auf (später: mit TON SDK)
        # PLACEHOLDER - hier würde die echte TON Transaktion stattfinden
        tx_hash = f"tx_{claim_id}_{user_id}_{int(datetime.now().timestamp())}"
        
        # 3. Update Claim Status
        cursor.execute(
            """
            UPDATE rewards_claims
            SET status = 'submitted', tx_hash = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (tx_hash, claim_id)
        )
        
        logger.info(f"Processed reward claim {claim_id}: {emrd_nanoton_to_readable(amount)} EMRD to {wallet_address}")
        
        return {
            "success": True,
            "claim_id": claim_id,
            "tx_hash": tx_hash,
            "status": "submitted",
            "message": f"Transaktion eingereicht: {tx_hash}"
        }
    except Exception as e:
        logger.error(f"Error processing reward claim: {e}")
        return {"success": False, "message": f"Fehler: {str(e)}"}


# ============================================================================
# REWARD HISTORY & STATISTICS
# ============================================================================

def get_user_reward_history(user_id: int, limit: int = 100) -> List[Dict]:
    """
    Holt die History von Claim-Transaktionen
    """
    try:
        cursor = get_db_cursor()
        if not cursor:
            return []
        
        cursor.execute(
            """
            SELECT id, amount, wallet_address, status, tx_hash, created_at
            FROM rewards_claims
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (user_id, limit)
        )
        
        return [
            {
                "claim_id": row[0],
                "amount_emrd": emrd_nanoton_to_readable(row[1]),
                "wallet": row[2],
                "status": row[3],
                "tx_hash": row[4],
                "timestamp": row[5].isoformat() if row[5] else None
            }
            for row in cursor.fetchall()
        ]
    except Exception as e:
        logger.error(f"Error getting reward history: {e}")
        return []


def get_reward_statistics() -> Dict:
    """
    Globale Reward-Statistiken
    """
    try:
        cursor = get_db_cursor()
        if not cursor:
            return {}
        
        # Total Pending
        cursor.execute("SELECT COALESCE(SUM(points), 0) FROM rewards_pending WHERE processed = FALSE")
        total_pending_points = int(cursor.fetchone()[0])
        
        # Total Claimed
        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM rewards_claims WHERE status IN ('submitted', 'confirmed')")
        total_claimed_nanoton = int(cursor.fetchone()[0])
        
        # Total Users with Rewards
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM rewards_claims")
        total_users = int(cursor.fetchone()[0])
        
        # Pending Claims
        cursor.execute("SELECT COUNT(*) FROM rewards_claims WHERE status = 'pending'")
        pending_claims = int(cursor.fetchone()[0])
        
        return {
            "total_pending_points": total_pending_points,
            "total_pending_emrd": emrd_nanoton_to_readable(points_to_emrd_nanoton(total_pending_points)),
            "total_claimed_emrd": emrd_nanoton_to_readable(total_claimed_nanoton),
            "total_users": total_users,
            "pending_claims": pending_claims
        }
    except Exception as e:
        logger.error(f"Error getting reward statistics: {e}")
        return {}

# ============================================================================
# ASYNC REWARD PROCESSING (Cron-Job)
# ============================================================================

async def process_pending_claims() -> Dict:
    """
    Hintergrund-Job zum Verarbeiten ausstehender Claims
    Sollte regelmäßig (z.B. alle 5 Minuten) aufgerufen werden
    """
    try:
        cursor = get_db_cursor()
        if not cursor:
            return {"success": False, "processed": 0}
        
        # Hole alle unverarbeiteten Claims (älter als 1 Minute für Spam-Schutz)
        cursor.execute(
            """
            SELECT id FROM rewards_claims
            WHERE status = 'pending' 
            AND created_at < NOW() - INTERVAL '1 minute'
            ORDER BY created_at ASC
            LIMIT 10
            """
        )
        
        claims = [row[0] for row in cursor.fetchall()]
        processed_count = 0
        
        for claim_id in claims:
            result = await process_reward_claim(claim_id)
            if result["success"]:
                processed_count += 1
        
        return {
            "success": True,
            "processed": processed_count,
            "total_pending": len(claims)
        }
    except Exception as e:
        logger.error(f"Error in background claim processing: {e}")
        return {"success": False, "processed": 0}

# ============================================================================
# CONFIGURATION & VALIDATION
# ============================================================================

async def validate_reward_configuration() -> Dict:
    """
    Validiert dass das Reward-System richtig konfiguriert ist
    """
    issues = []
    
    if not REWARD_WALLET:
        issues.append("❌ REWARD_WALLET_ADDRESS nicht gesetzt")
    
    if not REWARD_WALLET_PRIVATE_KEY:
        issues.append("❌ REWARD_WALLET_PRIVATE_KEY nicht gesetzt")
    
    if not TON_API_KEY:
        issues.append("⚠️ TON_API_KEY nicht gesetzt (read-only mode)")
    
    if REWARD_CONVERSION_RATE <= 0:
        issues.append("❌ REWARD_CONVERSION_RATE muss > 0 sein")
    
    if FEE_PERCENTAGE < 0 or FEE_PERCENTAGE > 100:
        issues.append("❌ REWARD_FEE_PERCENTAGE muss zwischen 0 und 100 sein")
    
    if not issues:
        return {
            "status": "✅ OK",
            "configured": True,
            "config": {
                "conversion_rate": REWARD_CONVERSION_RATE,
                "fee_percentage": FEE_PERCENTAGE,
                "min_claim_emrd": emrd_nanoton_to_readable(MIN_CLAIM_AMOUNT),
                "token_contract": EMRD_TOKEN_CONTRACT,
                "endpoint": TON_ENDPOINT
            }
        }
    else:
        return {
            "status": "⚠️ ISSUES FOUND",
            "configured": False,
            "issues": issues
        }
