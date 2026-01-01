"""
Trade DEX Bot Configuration
"""

import os
from typing import Optional

# ============================================================================
# BASIC CONFIGURATION
# ============================================================================

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/emerald_db")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ============================================================================
# DEX PROVIDER CONFIGURATION
# ============================================================================

# PancakeSwap (BSC)
PANCAKESWAP_ENABLED = True
BSC_RPC = os.getenv("BSC_RPC", "https://bsc-dataseed.binance.org")
PANCAKE_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
PANCAKE_ROUTER = "0x10ED43C718714eb2666A8B8038274567e06Cdc76"

# Aerodome (Evmos)
AERODOME_ENABLED = True
EVMOS_RPC = os.getenv("EVMOS_RPC", "https://evmos-mainnet-lcd.allthatnode.com")

# OKX Exchange
OKX_ENABLED = bool(os.getenv("OKX_API_KEY"))
OKX_API_KEY = os.getenv("OKX_API_KEY", "")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")

# ============================================================================
# TOKEN CONFIGURATION
# ============================================================================

# EMRD Token (main token for rewards)
EMRD_TOKEN_CONTRACT = os.getenv(
    "EMRD_TOKEN_CONTRACT",
    "EQAr2N2-VHHNMVTrLqWN1EQPnfBJ6D3aaILLaDT_kEEJ"  # TON Network
)

# Common tokens
TOKENS = {
    "BTC": {
        "symbol": "BTC",
        "decimals": 8,
        "bsc": "0x7130d2a12b9bcbfdd4467b4f3834a9009122a896",
        "evmos": "0x63743e39141f18563cc3b142855e935ae6c1dd93"
    },
    "ETH": {
        "symbol": "ETH",
        "decimals": 18,
        "bsc": "0x2170ed0880ac9a755fd29b2688956bd959e2000",
        "evmos": "0xd4949664b2b213f63c1f31b50f6676666a4d4472"
    },
    "EMRD": {
        "symbol": "EMRD",
        "decimals": 9,
        "bsc": "0x0000000000000000000000000000000000000000",  # TBD
        "evmos": "0x0000000000000000000000000000000000000000"  # TBD
    },
    "USDT": {
        "symbol": "USDT",
        "decimals": 6,
        "bsc": "0x55d398326f99059fF775485246999027B3197955",
        "evmos": "0x7ff4a56b32ee2e6280078e92b6d14e5eaa2a0b81"
    },
    "USDC": {
        "symbol": "USDC",
        "decimals": 6,
        "bsc": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
        "evmos": "0x2cc254ac582a8db798674c13a6c40407950d4a10"
    }
}

# ============================================================================
# TRADING CONFIGURATION
# ============================================================================

# Default slippage tolerance (percentage)
DEFAULT_SLIPPAGE = 0.5

# Minimum trade amount (prevents spam)
MIN_TRADE_AMOUNT = 0.001

# Maximum trade amount (safety limit)
MAX_TRADE_AMOUNT = 10000

# Default DEX priority
DEX_PRIORITY = ["pancakeswap", "aerodome", "okx"]

# ============================================================================
# STRATEGY CONFIGURATION
# ============================================================================

# DCA (Dollar Cost Averaging) Strategy
DCA_CONFIG = {
    "min_interval_hours": 1,
    "max_interval_hours": 720,  # 30 days
    "min_amount": 10,
    "max_amount": 10000,
    "supported_dex": ["pancakeswap", "aerodome"]
}

# Grid Trading Strategy
GRID_CONFIG = {
    "min_grid_levels": 3,
    "max_grid_levels": 50,
    "min_spread": 0.5,  # %
    "max_spread": 20,   # %
    "supported_dex": ["pancakeswap", "aerodome"]
}

# Scheduled Swap Strategy
SCHEDULED_CONFIG = {
    "min_interval_minutes": 5,
    "max_interval_minutes": 10080,  # 7 days
    "supported_dex": ["pancakeswap", "aerodome"]
}

# ============================================================================
# ALERT CONFIGURATION
# ============================================================================

# Price alert limits
ALERT_CONFIG = {
    "max_alerts_per_user": 20,
    "min_price_change": 0.01,  # $0.01
    "max_price_targets": 100,
    "alert_check_interval_minutes": 5,
    "notification_cooldown_minutes": 5
}

# ============================================================================
# REWARD CONFIGURATION
# ============================================================================

# Reward wallet for distributing EMRD
REWARD_WALLET = os.getenv("REWARD_WALLET_ADDRESS", "")
REWARD_WALLET_PRIVATE_KEY = os.getenv("REWARD_WALLET_PRIVATE_KEY", "")

# Reward amounts
REWARD_AMOUNTS = {
    "swap": 100,              # Points for executing a swap
    "large_swap": 500,        # Points for swap > $1000
    "add_liquidity": 200,     # Points for adding liquidity
    "remove_liquidity": 50,   # Points for removing liquidity
    "create_alert": 25,       # Points for creating alert
    "create_strategy": 150,   # Points for creating strategy
    "strategy_execution": 50, # Points per strategy execution
    "daily_login": 50,        # Points for daily login
    "portfolio_update": 10,   # Points for portfolio interaction
}

# Min/max reward claim
MIN_CLAIM_AMOUNT = 100000000  # 0.1 EMRD in nanotons
MIN_CLAIM_INTERVAL_HOURS = 24

# ============================================================================
# API LIMITS
# ============================================================================

# Rate limiting
API_RATE_LIMITS = {
    "default": {"requests": 100, "window_seconds": 60},
    "swap": {"requests": 10, "window_seconds": 60},
    "pricing": {"requests": 30, "window_seconds": 60},
    "pools": {"requests": 20, "window_seconds": 60},
}

# Request timeouts
REQUEST_TIMEOUT = 30

# ============================================================================
# CACHE CONFIGURATION
# ============================================================================

# Cache durations in seconds
CACHE_DURATIONS = {
    "pools": 300,          # 5 minutes
    "prices": 60,          # 1 minute
    "market_stats": 300,   # 5 minutes
    "balances": 300,       # 5 minutes
}

# ============================================================================
# NOTIFICATION CONFIGURATION
# ============================================================================

NOTIFICATION_CONFIG = {
    "enabled": True,
    "channels": ["telegram", "email"],  # Optional: email
    "telegram_notifications": True,
    "digest_interval_hours": 24,
    "important_price_change_threshold": 5,  # % change
}

# ============================================================================
# SECURITY CONFIGURATION
# ============================================================================

# Wallet operations
SECURITY_CONFIG = {
    "require_wallet_connection": False,  # For on-chain swaps
    "auto_approve_threshold": 100,  # Auto-approve swaps < $100
    "enable_2fa": False,
    "session_timeout_minutes": 60,
}

# ============================================================================
# BLOCKCHAIN ADDRESSES
# ============================================================================

# Common contract addresses
CONTRACTS = {
    "BSC": {
        "WBNB": "0xbb4CdB9CBd36B01bD1cbaebF2De08d9173bc095c",
        "BUSD": "0xe9e7cea3dedca5984780bafc599bd69add087d56",
        "PANCAKE_ROUTER": "0x10ED43C718714eb2666A8B8038274567e06Cdc76",
        "PANCAKE_FACTORY": "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
    },
    "EVMOS": {
        "WEVMOS": "0xD4949664B2B213f63C1f31b50F6676666A4D4472",
        "USDC": "0x2Cc254Ac582A8dB798674C13A6c40407950d4a10",
        "ATOM": "0xB00C137F5f1e4D23B00aEF50cdfFDeF192e7B85a",
    }
}

# ============================================================================
# FEATURE FLAGS
# ============================================================================

FEATURES = {
    "swap_enabled": True,
    "liquidity_enabled": True,
    "strategies_enabled": True,
    "alerts_enabled": True,
    "portfolio_tracking_enabled": True,
    "market_data_enabled": True,
    "rewards_enabled": True,
    "referral_program_enabled": False,
}

# ============================================================================
# DEVELOPMENT & DEBUG
# ============================================================================

DEBUG = os.getenv("DEBUG", "False").lower() == "true"
TESTNET = os.getenv("TESTNET", "False").lower() == "true"

if TESTNET:
    # Testnet configuration
    BSC_RPC = "https://data-seed-prebsc-1-e-bn-146-28-228-127.mempoolapi.com:2053"
    EVMOS_RPC = "https://testnet-lcd.evmos.org"
