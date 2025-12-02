"""EMRD Token Configuration for Affiliate Bot"""

import os

# EMRD Token Contract
EMRD_CONTRACT = os.getenv(
    "EMRD_CONTRACT",
    "EQA0rJDTy_2sS30KxQW8HO0_ERqmOGUhMWlwdL-2RpDmCrK5"
)

# TON Network
TON_MAINNET_API = "https://toncenter.com/api/v2"
TON_TESTNET_API = "https://testnet.toncenter.com/api/v2"
IS_TESTNET = os.getenv("TON_TESTNET", "false").lower() == "true"

# Affiliate Configuration
AFFILIATE_CONFIG = {
    "minimum_payout": 1000,  # EMRD
    "tier_thresholds": {
        "bronze": {"min": 0, "max": 999, "commission": 0.05, "emoji": "🥉"},
        "silver": {"min": 1000, "max": 4999, "commission": 0.10, "emoji": "🥈"},
        "gold": {"min": 5000, "max": 9999, "commission": 0.15, "emoji": "🥇"},
        "platinum": {"min": 10000, "max": float('inf'), "commission": 0.20, "emoji": "🔶"}
    },
    "bonus_thresholds": {
        100: {"bonus_percent": 10, "description": "10% Bonus bei 100+ Referrals"},
        250: {"bonus_percent": 20, "description": "20% Bonus bei 250+ Referrals"},
        500: {"bonus_percent": 30, "description": "30% Bonus bei 500+ Referrals"}
    }
}

# Commission Structure
def get_commission_rate(total_earned: float) -> float:
    """Get commission rate based on tier"""
    config = AFFILIATE_CONFIG["tier_thresholds"]
    for tier_name, tier_data in config.items():
        if tier_data["min"] <= total_earned <= tier_data["max"]:
            return tier_data["commission"]
    return config["bronze"]["commission"]


def get_tier_info(total_earned: float) -> dict:
    """Get tier information"""
    config = AFFILIATE_CONFIG["tier_thresholds"]
    for tier_name, tier_data in config.items():
        if tier_data["min"] <= total_earned <= tier_data["max"]:
            return {
                "tier": tier_name,
                "emoji": tier_data["emoji"],
                "commission_rate": tier_data["commission"],
                "next_tier": None if tier_name == "platinum" else get_next_tier(tier_name),
                "earned_in_tier": total_earned - tier_data["min"]
            }
    return {
        "tier": "platinum",
        "emoji": config["platinum"]["emoji"],
        "commission_rate": config["platinum"]["commission"],
        "next_tier": None,
        "earned_in_tier": total_earned - config["platinum"]["min"]
    }


def get_next_tier(current_tier: str) -> dict:
    """Get next tier information"""
    tiers = ["bronze", "silver", "gold", "platinum"]
    current_index = tiers.index(current_tier)
    
    if current_index >= len(tiers) - 1:
        return None
    
    next_tier = tiers[current_index + 1]
    config = AFFILIATE_CONFIG["tier_thresholds"]
    next_data = config[next_tier]
    
    return {
        "tier": next_tier,
        "emoji": next_data["emoji"],
        "min_earned": next_data["min"],
        "commission_rate": next_data["commission"]
    }


def get_referral_bonus(referral_count: int, tier: str) -> dict:
    """Get referral bonus"""
    bonus_config = AFFILIATE_CONFIG["bonus_thresholds"]
    
    for threshold in sorted(bonus_config.keys(), reverse=True):
        if referral_count >= threshold:
            return {
                "threshold": threshold,
                "bonus_percent": bonus_config[threshold]["bonus_percent"],
                "description": bonus_config[threshold]["description"]
            }
    
    return {"threshold": 0, "bonus_percent": 0, "description": "Keine Boni"}


# TON Connect Configuration
TONCONNECT_CONFIG = {
    "manifestUrl": os.getenv("TONCONNECT_MANIFEST", "https://your-domain.com/tonconnect-manifest.json"),
    "network": "mainnet" if not IS_TESTNET else "testnet"
}

# Payment Configuration
PAYMENT_CONFIG = {
    "ton": {
        "enabled": True,
        "contract": EMRD_CONTRACT,
        "decimals": 9,
        "network": "mainnet" if not IS_TESTNET else "testnet"
    },
    "min_payout": AFFILIATE_CONFIG["minimum_payout"],
    "max_payout_per_request": 50000  # EMRD
}

# Webhook Configuration (for TON TX verification)
WEBHOOK_CONFIG = {
    "enabled": os.getenv("WEBHOOK_ENABLED", "true").lower() == "true",
    "secret": os.getenv("WEBHOOK_SECRET", ""),
    "url": os.getenv("WEBHOOK_URL", "")
}

# Logging
LOGGING_CONFIG = {
    "level": os.getenv("LOG_LEVEL", "INFO"),
    "file": "logs/affiliate_bot.log",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
}


# Export functions for easy access
def get_tier_emoji(total_earned: float) -> str:
    """Get emoji for tier"""
    return get_tier_info(total_earned)["emoji"]


def get_tier_name(total_earned: float) -> str:
    """Get tier name"""
    return get_tier_info(total_earned)["tier"].upper()


if __name__ == "__main__":
    # Test
    print("💚 EMRD Affiliate Config")
    print(f"EMRD Contract: {EMRD_CONTRACT}")
    print(f"TON Network: {'TESTNET' if IS_TESTNET else 'MAINNET'}")
    print(f"Minimum Payout: {AFFILIATE_CONFIG['minimum_payout']} EMRD")
    print()
    
    # Example calculations
    test_earnings = [500, 1500, 5000, 15000]
    for earned in test_earnings:
        tier_info = get_tier_info(earned)
        bonus = get_referral_bonus(100, tier_info["tier"])
        print(f"Earned: {earned} EMRD")
        print(f"  → Tier: {tier_info['emoji']} {tier_info['tier'].upper()}")
        print(f"  → Commission: {tier_info['commission_rate']*100}%")
        print(f"  → Bonus (100 refs): {bonus['bonus_percent']}% ({bonus['description']})")
        print()
