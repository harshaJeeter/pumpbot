"""
Pump.fun Trading Bot - Configuration
All thresholds, API keys, and settings in one place.
"""

import os

# ═══════════════════════════════════════════════════════════════
# WALLET & RPC
# ═══════════════════════════════════════════════════════════════
WALLET_PRIVATE_KEY = os.environ.get("WALLET_PRIVATE_KEY", "")
WALLET_PUBLIC_KEY = os.environ.get("WALLET_PUBLIC_KEY", "2aWA3rTGesJu7bZVtepRrM4oAgVP4vQvfW7Qs5nWpycM")
HELIUS_RPC_URL = os.environ.get("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=YOUR_KEY")
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "YOUR_KEY")

# ═══════════════════════════════════════════════════════════════
# TRADING PARAMETERS
# ═══════════════════════════════════════════════════════════════
BUY_AMOUNT_SOL = 0.03              # SOL per trade (conservative)
MIN_SOL_BALANCE = 0.1              # Never go below this (reserve for rent/fees)
MAX_ACTIVE_POSITIONS = 5           # Max simultaneous positions
MAX_BUYS_PER_HOUR = 3             # Rate limit buys
SLIPPAGE_BPS = 2500               # 25% slippage for pump.fun tokens (high volatility)

# ═══════════════════════════════════════════════════════════════
# SCORING THRESHOLDS (stricter = safer)
# ═══════════════════════════════════════════════════════════════
MIN_SCORE = 72                     # Min composite score to buy (was 50, too low)
MIN_MC_USD = 8_000                 # Min market cap (lowered since WS catches early)
MAX_MC_USD = 80_000                # Max market cap (still early enough for gains)
MIN_LIQUIDITY_USD = 2_000          # Min liquidity (lowered for bonding curve tokens)
MIN_HOLDERS = 15                   # Min unique holders (lowered for early tokens)
MAX_TOP_HOLDER_PCT = 25            # Max % any single holder can have
MIN_VOLUME_5M = 200                # Min 5-minute volume in USD
MIN_TOKEN_AGE_SECONDS = 60         # Token must be at least 1 min old (avoid instant rugs)
MAX_TOKEN_AGE_SECONDS = 1800       # Don't buy tokens older than 30 min

# ═══════════════════════════════════════════════════════════════
# SELL LOGIC
# ═══════════════════════════════════════════════════════════════
# Take profit tiers
TAKE_PROFIT_1_PCT = 50             # Sell 40% at +50%
TAKE_PROFIT_1_SIZE = 0.40
TAKE_PROFIT_2_PCT = 150            # Sell 30% at +150%
TAKE_PROFIT_2_SIZE = 0.30
TAKE_PROFIT_3_PCT = 400            # Sell remaining at +400%
TAKE_PROFIT_3_SIZE = 1.0           # Everything left

# Stop loss
STOP_LOSS_PCT = -35                # Hard stop at -35%

# Trailing stop
TRAILING_STOP_ACTIVATE_PCT = 25    # Activate trailing stop at +25%
TRAILING_STOP_DISTANCE_PCT = 12    # Trail 12% below peak

# Position monitoring
POSITION_CHECK_INTERVAL = 15       # Check positions every 15 seconds
STALE_POSITION_HOURS = 4           # Force-close positions older than 4 hours

# ═══════════════════════════════════════════════════════════════
# SCORING WEIGHTS (must sum to 1.0)
# ═══════════════════════════════════════════════════════════════
WEIGHT_MOMENTUM = 0.30             # How fast MC is growing
WEIGHT_HOLDER = 0.25               # Holder distribution quality
WEIGHT_SOCIAL = 0.15               # Twitter/Telegram/Website
WEIGHT_LIQUIDITY = 0.15            # Liquidity depth
WEIGHT_NAME = 0.10                 # Meme quality of name
WEIGHT_VOLUME = 0.05               # Volume activity

# ═══════════════════════════════════════════════════════════════
# RUG DETECTION
# ═══════════════════════════════════════════════════════════════
RUG_INDICATORS = {
    "creator_holds_over_pct": 15,         # Creator holding >15% = rug risk
    "top_10_hold_over_pct": 60,           # Top 10 wallets holding >60%
    "mint_authority_enabled": True,        # Mint authority not revoked = danger
    "freeze_authority_enabled": True,      # Freeze authority = danger
    "no_socials": True,                    # No twitter/website = red flag
    "name_looks_scammy": True,            # Random hex names, copycats
    "bundled_launch": True,               # Multiple buys in same block (coordinated)
}

# ═══════════════════════════════════════════════════════════════
# APIS
# ═══════════════════════════════════════════════════════════════
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
JUPITER_API_BASE = "https://api.jup.ag/swap/v2"
JUPITER_QUOTE_API = "https://api.jup.ag/swap/v1/quote"
JUPITER_SWAP_API = "https://api.jup.ag/swap/v1/swap"
PUMPPORTAL_API = "https://pumpportal.fun/api"
PUMPFUN_API = "https://frontend-api-v3.pump.fun"
SOLANA_FM_API = "https://api.solana.fm"

# Token addresses
SOL_MINT = "So11111111111111111111111111111111111111112"  # Wrapped SOL

# ═══════════════════════════════════════════════════════════════
# TELEGRAM NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

# ═══════════════════════════════════════════════════════════════
# DATA PATHS
# ═══════════════════════════════════════════════════════════════
DATA_DIR = os.environ.get("DATA_DIR", "./data")
TRADE_LOG_FILE = f"{DATA_DIR}/trade_log.json"
POSITIONS_FILE = f"{DATA_DIR}/positions.json"
BLOCKLIST_FILE = f"{DATA_DIR}/blocklist.json"
STATS_FILE = f"{DATA_DIR}/stats.json"
SEEN_TOKENS_FILE = f"{DATA_DIR}/seen_tokens.json"

# ═══════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════
DASHBOARD_PORT = 4001
DASHBOARD_HOST = "0.0.0.0"
