# Pump.fun Auto-Trading Bot v2

A Solana automated trading bot for pump.fun tokens. Scans, scores, buys, and sells tokens automatically with built-in rug detection.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        BOT CORE (bot.py)                      │
│                                                              │
│  Scanner ──→ Enrich ──→ Rug Check ──→ Score ──→ Buy         │
│     ↑                                              ↓         │
│     │         ┌─────────────────────────────┐     │         │
│     └─────────│   Position Manager          │←────┘         │
│               │   (TP / SL / Trailing)      │               │
│               └────────────┬────────────────┘               │
│                            ↓                                 │
│                         SELL (Jupiter / PumpPortal)           │
└──────────────────────────────────────────────────────────────┘
         │                                           │
         ▼                                           ▼
    Dashboard (port 4001)                   Telegram Alerts
```

## Key Improvements Over v1

| Issue | v1 (Broken) | v2 (Fixed) |
|-------|-------------|------------|
| Name Resolution | Always "?" | 3-source resolver (DexScreener → Pump.fun API → Helius DAS) |
| Sell Path | 100% failure | Jupiter V2 API + PumpPortal fallback |
| Buy Failures (90%) | No pre-checks | Balance check + bonding curve check + graduation check |
| Duplicate Buys | No dedup | CA-based dedup (never buy same token twice) |
| Rug Detection | None | Multi-factor analysis (creator %, authorities, socials, name patterns) |
| Scoring | min=50, broken | min=72, working name/momentum/holder scoring |
| MC Range | $5K-$50K | $15K-$80K (avoids dead launches) |
| Position Staleness | None | Auto-close after 4 hours |
| Wallet Balance | Not checked | Pre-flight balance check |

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Main orchestrator |
| `config.py` | All settings/thresholds |
| `scanner.py` | Token discovery (DexScreener + pump.fun) |
| `token_resolver.py` | Name/symbol resolution (fixes "?" issue) |
| `scoring_engine.py` | Multi-factor scoring (0-100) |
| `rug_detector.py` | Rug pull detection |
| `trader.py` | Buy/sell execution (PumpPortal + Jupiter) |
| `position_manager.py` | Position tracking & sell logic |
| `data_store.py` | Persistent storage |
| `notifier.py` | Telegram alerts |
| `dashboard.py` | Web UI (port 4001) |

## Quick Start

```bash
# 1. Copy and fill in your environment variables
cp .env.example .env
nano .env

# 2. Run the bot
chmod +x start.sh
./start.sh
```

## Manual Start (without script)

```bash
pip install -r requirements.txt
python3 bot.py
```

## Configuration

Edit `config.py` to tune:

- **BUY_AMOUNT_SOL** (0.03): How much SOL per trade
- **MIN_SCORE** (72): Minimum score to buy (higher = pickier)
- **MIN_MC_USD** (15,000): Skip tokens below this market cap
- **MAX_MC_USD** (80,000): Skip tokens above this (too late)
- **STOP_LOSS_PCT** (-35%): Hard stop loss
- **TAKE_PROFIT tiers**: Sell portions at +50%, +150%, +400%
- **TRAILING_STOP**: Activates at +25%, trails 12% below peak

## How It Finds Good Coins

1. **Scanner** checks DexScreener + pump.fun every 8 seconds for new tokens
2. **Resolver** fetches real name/symbol (never buys unknowns)
3. **Basic Filters** eliminate tokens outside MC range, graduated, or too old/new
4. **Rug Detector** rejects tokens with:
   - Creator holding >15%
   - Top 10 wallets holding >60%
   - Active mint/freeze authorities
   - No socials (twitter/website)
   - Scam name patterns
5. **Scoring Engine** rates remaining tokens (0-100) on:
   - Momentum (30%): How fast MC is growing
   - Holders (25%): Distribution quality
   - Social (15%): Twitter/Telegram/Website presence
   - Liquidity (15%): Exit liquidity depth
   - Name (10%): Meme virality potential
   - Volume (5%): Trading activity

Only tokens scoring **72+** with rug score **<50** get bought.

## Dashboard

Access at `http://YOUR_VPS_IP:4001`

Shows: scanner stats, P&L, active positions, recent trades, wallet balance.

## Telegram Setup

1. Message @BotFather on Telegram, create a bot, get the token
2. Message @userinfobot to get your chat ID
3. Add both to `.env`

## VPS Deployment

```bash
# Run in background with systemd
sudo cp pumpbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pumpbot
sudo systemctl start pumpbot

# Check logs
journalctl -u pumpbot -f
```
