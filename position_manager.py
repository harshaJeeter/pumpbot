"""
Position Manager - Tracks open positions and executes sell logic.
Handles take-profit, stop-loss, trailing stops, and stale position cleanup.
"""

import asyncio
import json
import time
import os
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field

import httpx

import config

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open trading position."""
    mint: str
    name: str
    symbol: str
    buy_price_sol: float          # SOL price at buy
    buy_mc_usd: float             # Market cap at buy
    buy_amount_sol: float         # SOL spent
    token_amount: int             # Tokens received
    buy_time: float               # Unix timestamp
    buy_tx: str                   # Buy transaction signature
    current_mc_usd: float = 0.0
    current_price_sol: float = 0.0
    peak_mc_usd: float = 0.0     # All-time high MC since buy
    peak_price_sol: float = 0.0
    pnl_pct: float = 0.0
    trailing_active: bool = False
    sells_executed: List[Dict] = field(default_factory=list)  # History of partial sells
    remaining_pct: float = 1.0    # What % of position remains
    status: str = "active"        # active, sold, stale, error
    last_check: float = 0.0


class PositionManager:
    """
    Manages all open positions:
    - Monitors price changes
    - Executes take-profit tiers
    - Manages trailing stops
    - Handles stop-losses
    - Cleans up stale positions
    """

    def __init__(self, trader):
        self.trader = trader
        self.positions: Dict[str, Position] = {}  # mint -> Position
        self._running = False
        self._load_positions()

    def add_position(self, mint: str, name: str, symbol: str,
                     buy_mc_usd: float, buy_amount_sol: float,
                     token_amount: int, buy_tx: str):
        """Add a new position after successful buy."""
        if mint in self.positions:
            # Update existing (shouldn't happen with dedup, but safety)
            existing = self.positions[mint]
            existing.token_amount += token_amount
            existing.buy_amount_sol += buy_amount_sol
            logger.warning(f"Updated existing position for {symbol} (duplicate buy)")
        else:
            pos = Position(
                mint=mint,
                name=name,
                symbol=symbol,
                buy_price_sol=buy_amount_sol,  # Will be refined
                buy_mc_usd=buy_mc_usd,
                buy_amount_sol=buy_amount_sol,
                token_amount=token_amount,
                buy_time=time.time(),
                buy_tx=buy_tx,
                current_mc_usd=buy_mc_usd,
                peak_mc_usd=buy_mc_usd,
            )
            self.positions[mint] = pos
            logger.info(f"New position: {symbol} @ ${buy_mc_usd:,.0f} MC ({buy_amount_sol} SOL)")

        self._save_positions()

    async def start_monitoring(self):
        """Start position monitoring loop."""
        self._running = True
        logger.info(f"Position monitor started ({len(self.positions)} active positions)")

        while self._running:
            try:
                await self._check_all_positions()
            except Exception as e:
                logger.error(f"Position monitor error: {e}")

            await asyncio.sleep(config.POSITION_CHECK_INTERVAL)

    def stop(self):
        """Stop monitoring."""
        self._running = False

    async def _check_all_positions(self):
        """Check all active positions for sell conditions."""
        active = [p for p in self.positions.values() if p.status == "active"]

        for pos in active:
            try:
                await self._check_position(pos)
            except Exception as e:
                logger.error(f"Error checking position {pos.symbol}: {e}")

    async def _check_position(self, pos: Position):
        """Check a single position for sell triggers."""
        now = time.time()
        pos.last_check = now

        # Get current market data
        mc = await self._get_current_mc(pos.mint)
        if mc is None or mc <= 0:
            # Can't get price - check if stale
            if now - pos.buy_time > config.STALE_POSITION_HOURS * 3600:
                logger.warning(f"Stale position: {pos.symbol} - force selling")
                await self._execute_sell(pos, 1.0, "STALE_POSITION")
            return

        # Update position state
        pos.current_mc_usd = mc
        if mc > pos.peak_mc_usd:
            pos.peak_mc_usd = mc

        # Calculate P&L
        if pos.buy_mc_usd > 0:
            pos.pnl_pct = ((mc - pos.buy_mc_usd) / pos.buy_mc_usd) * 100

        # ═══════════════════════════════════════════
        # SELL DECISION LOGIC
        # ═══════════════════════════════════════════

        # 1. STOP LOSS
        if pos.pnl_pct <= config.STOP_LOSS_PCT:
            logger.info(f"STOP LOSS triggered for {pos.symbol}: {pos.pnl_pct:.1f}%")
            await self._execute_sell(pos, 1.0, "STOP_LOSS")
            return

        # 2. TAKE PROFIT TIER 3 (sell everything remaining at +400%)
        if pos.pnl_pct >= config.TAKE_PROFIT_3_PCT and pos.remaining_pct > 0:
            tp3_done = any(s.get("reason") == "TP3" for s in pos.sells_executed)
            if not tp3_done:
                logger.info(f"TP3 triggered for {pos.symbol}: +{pos.pnl_pct:.1f}%")
                await self._execute_sell(pos, config.TAKE_PROFIT_3_SIZE, "TP3")
                return

        # 3. TAKE PROFIT TIER 2 (sell 30% at +150%)
        if pos.pnl_pct >= config.TAKE_PROFIT_2_PCT:
            tp2_done = any(s.get("reason") == "TP2" for s in pos.sells_executed)
            if not tp2_done and pos.remaining_pct > 0.5:
                logger.info(f"TP2 triggered for {pos.symbol}: +{pos.pnl_pct:.1f}%")
                await self._execute_sell(pos, config.TAKE_PROFIT_2_SIZE, "TP2")
                return

        # 4. TAKE PROFIT TIER 1 (sell 40% at +50%)
        if pos.pnl_pct >= config.TAKE_PROFIT_1_PCT:
            tp1_done = any(s.get("reason") == "TP1" for s in pos.sells_executed)
            if not tp1_done:
                logger.info(f"TP1 triggered for {pos.symbol}: +{pos.pnl_pct:.1f}%")
                await self._execute_sell(pos, config.TAKE_PROFIT_1_SIZE, "TP1")
                return

        # 5. TRAILING STOP
        if pos.pnl_pct >= config.TRAILING_STOP_ACTIVATE_PCT:
            pos.trailing_active = True

        if pos.trailing_active and pos.peak_mc_usd > 0:
            drawdown_from_peak = ((pos.peak_mc_usd - mc) / pos.peak_mc_usd) * 100
            if drawdown_from_peak >= config.TRAILING_STOP_DISTANCE_PCT:
                logger.info(
                    f"TRAILING STOP for {pos.symbol}: "
                    f"peak ${pos.peak_mc_usd:,.0f} -> now ${mc:,.0f} "
                    f"({drawdown_from_peak:.1f}% drawdown)"
                )
                await self._execute_sell(pos, 1.0, "TRAILING_STOP")
                return

        # 6. STALE POSITION (been too long, just exit)
        hours_held = (now - pos.buy_time) / 3600
        if hours_held > config.STALE_POSITION_HOURS:
            logger.info(f"STALE position {pos.symbol}: held {hours_held:.1f}h, selling")
            await self._execute_sell(pos, 1.0, "STALE_TIMEOUT")
            return

        self._save_positions()

    async def _execute_sell(self, pos: Position, size_pct: float, reason: str):
        """Execute a sell for a position."""
        sell_pct = min(size_pct, pos.remaining_pct)
        if sell_pct <= 0:
            return

        logger.info(f"Selling {sell_pct*100:.0f}% of {pos.symbol} (reason: {reason})")

        result = await self.trader.sell_token(pos.mint, sell_pct)

        sell_record = {
            "time": time.time(),
            "reason": reason,
            "pct_sold": sell_pct,
            "pnl_at_sell": pos.pnl_pct,
            "mc_at_sell": pos.current_mc_usd,
            "success": result["success"],
            "tx": result.get("tx_signature", ""),
            "sol_received": result.get("sol_received", 0),
            "error": result.get("error", ""),
        }
        pos.sells_executed.append(sell_record)

        if result["success"]:
            pos.remaining_pct -= sell_pct
            if pos.remaining_pct <= 0.01:  # Basically sold everything
                pos.status = "sold"
                pos.remaining_pct = 0
                logger.info(f"Position CLOSED: {pos.symbol} (reason: {reason}, P&L: {pos.pnl_pct:+.1f}%)")
        else:
            logger.error(f"Sell FAILED for {pos.symbol}: {result.get('error', 'unknown')}")
            # Don't mark as sold - will retry next cycle

        self._save_positions()

    async def _get_current_mc(self, mint: str) -> Optional[float]:
        """Get current market cap for a token."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    pairs = data.get("pairs") or []
                    if pairs:
                        return float(pairs[0].get("marketCap", 0) or 0)
        except Exception as e:
            logger.debug(f"MC fetch failed for {mint}: {e}")
        return None

    def get_active_count(self) -> int:
        """Get number of active positions."""
        return sum(1 for p in self.positions.values() if p.status == "active")

    def get_all_positions(self) -> List[Dict]:
        """Get all positions as dicts (for dashboard)."""
        result = []
        for pos in self.positions.values():
            d = asdict(pos)
            d["age_hours"] = (time.time() - pos.buy_time) / 3600
            result.append(d)
        return sorted(result, key=lambda x: x["buy_time"], reverse=True)

    def get_stats(self) -> Dict:
        """Get trading statistics."""
        all_pos = list(self.positions.values())
        closed = [p for p in all_pos if p.status == "sold"]
        active = [p for p in all_pos if p.status == "active"]

        total_invested = sum(p.buy_amount_sol for p in all_pos)
        total_returned = sum(
            sum(s.get("sol_received", 0) for s in p.sells_executed if s.get("success"))
            for p in all_pos
        )

        wins = [p for p in closed if p.pnl_pct > 0]
        losses = [p for p in closed if p.pnl_pct <= 0]

        return {
            "total_trades": len(all_pos),
            "active_positions": len(active),
            "closed_positions": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / max(len(closed), 1) * 100,
            "total_invested_sol": total_invested,
            "total_returned_sol": total_returned,
            "net_pnl_sol": total_returned - total_invested,
            "avg_pnl_pct": sum(p.pnl_pct for p in closed) / max(len(closed), 1),
            "best_trade_pct": max((p.pnl_pct for p in closed), default=0),
            "worst_trade_pct": min((p.pnl_pct for p in closed), default=0),
        }

    def _save_positions(self):
        """Save positions to disk."""
        os.makedirs(config.DATA_DIR, exist_ok=True)
        data = {}
        for mint, pos in self.positions.items():
            data[mint] = asdict(pos)

        try:
            with open(config.POSITIONS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save positions: {e}")

    def _load_positions(self):
        """Load positions from disk."""
        if not os.path.exists(config.POSITIONS_FILE):
            return

        try:
            with open(config.POSITIONS_FILE, "r") as f:
                data = json.load(f)

            for mint, pos_data in data.items():
                # Remove any extra fields that aren't in Position
                valid_fields = {f.name for f in Position.__dataclass_fields__.values()}
                filtered = {k: v for k, v in pos_data.items() if k in valid_fields}
                self.positions[mint] = Position(**filtered)

            logger.info(f"Loaded {len(self.positions)} positions from disk")
        except Exception as e:
            logger.error(f"Failed to load positions: {e}")
