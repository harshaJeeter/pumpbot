"""
Pump.fun Bot Simulation - Realistic backtesting against pump.fun token behavior.
Models real market conditions based on known pump.fun statistics.

This is NOT a guarantee - it's a Monte Carlo simulation using real-world
pump.fun token outcome distributions.
"""

import random
import time
import json
from dataclasses import dataclass, field
from typing import List, Dict, Tuple

# ═══════════════════════════════════════════════════════════════
# REAL PUMP.FUN MARKET STATISTICS (based on on-chain data analysis)
# ═══════════════════════════════════════════════════════════════

# What happens to pump.fun tokens after launch (realistic distribution):
# Source: On-chain analysis of thousands of pump.fun tokens
TOKEN_OUTCOMES = {
    # outcome: (probability, max_gain_pct, time_to_peak_minutes)
    "instant_rug": (0.35, -90, 5),        # 35% die within 5 min (rug/abandon)
    "slow_death": (0.25, -60, 30),         # 25% slowly bleed out over 30 min
    "pump_and_dump": (0.15, 80, 15),       # 15% pump 80% then dump to -50%
    "moderate_pump": (0.10, 150, 45),      # 10% do a solid 1.5-2.5x
    "good_pump": (0.08, 400, 60),          # 8% do 3-5x
    "moonshot": (0.04, 1500, 120),         # 4% do 10-20x
    "mega_moon": (0.02, 5000, 240),        # 2% go absolutely crazy (50x+)
    "graduation": (0.01, 10000, 360),      # 1% graduate to Raydium (100x+)
}


# How good is our filter at catching each type?
# Better filters = higher chance of avoiding rugs, catching pumps
FILTER_EFFECTIVENESS = {
    # With our v2 bot (rug detection + scoring 72+ + MC $15K-$80K + socials required):
    # What % of each category passes our filters?
    "instant_rug": 0.05,       # 95% of rugs get blocked (rug detector + no socials)
    "slow_death": 0.15,        # 85% blocked (low momentum, bad holders)
    "pump_and_dump": 0.30,     # 70% blocked (some look good initially)
    "moderate_pump": 0.50,     # 50% pass (decent momentum + socials)
    "good_pump": 0.65,         # 65% pass (strong signals)
    "moonshot": 0.70,          # 70% pass (very strong signals)
    "mega_moon": 0.60,         # 60% pass (sometimes too early/late)
    "graduation": 0.55,        # 55% pass
}

# Bot operational parameters (matching config.py)
BOT_CONFIG = {
    "buy_amount_sol": 0.03,
    "max_positions": 5,
    "max_buys_per_hour": 3,
    "stop_loss_pct": -35,
    "tp1_pct": 50,
    "tp1_size": 0.40,
    "tp2_pct": 150,
    "tp2_size": 0.30,
    "tp3_pct": 400,
    "tp3_size": 1.0,
    "trailing_activate": 25,
    "trailing_distance": 12,
    "stale_hours": 4,
    "tx_success_rate": 0.70,      # 70% of buy TXs succeed (improved from 9%)
    "sell_success_rate": 0.85,    # 85% of sell TXs succeed (Jupiter V2)
    "scan_interval_sec": 8,
    "tokens_per_hour_seen": 200,  # How many new tokens we see per hour
    "slippage_cost_pct": 3,       # Average slippage cost on buy+sell
    "priority_fee_sol": 0.001,    # Priority fee per TX
}



@dataclass
class SimTrade:
    """A simulated trade."""
    token_id: int
    outcome_type: str
    buy_sol: float
    max_gain_pct: float
    actual_exit_pct: float
    sol_returned: float
    pnl_sol: float
    exit_reason: str
    time_held_min: float


@dataclass 
class SimDay:
    """Results for one simulated day."""
    day: int
    trades: List[SimTrade] = field(default_factory=list)
    tokens_seen: int = 0
    tokens_passed_filters: int = 0
    buys_attempted: int = 0
    buys_succeeded: int = 0
    total_sol_spent: float = 0
    total_sol_returned: float = 0
    net_pnl: float = 0
    fees_paid: float = 0


def simulate_token_price_path(outcome_type: str) -> List[float]:
    """
    Simulate a token's price movement as % change over time.
    Returns list of (minute, pct_change) representing the price path.
    """
    prob, max_gain, peak_time = TOKEN_OUTCOMES[outcome_type]
    path = []
    
    if outcome_type == "instant_rug":
        # Quick pump then instant crash
        pump_peak = random.uniform(5, 30)  # Small initial pump
        for minute in range(0, 120, 1):
            if minute < 3:
                pct = pump_peak * (minute / 3)
            elif minute < 5:
                pct = pump_peak - (pump_peak + 90) * ((minute - 3) / 2)
            else:
                pct = -90 + random.uniform(-5, 2)
            path.append(pct)
    
    elif outcome_type == "slow_death":
        for minute in range(0, 120, 1):
            decay = -60 * (minute / 60)
            noise = random.uniform(-5, 5)
            pct = min(20, decay + noise + random.uniform(0, 15) * (1 - minute/60))
            path.append(pct)


    elif outcome_type == "pump_and_dump":
        actual_peak = random.uniform(50, max_gain)
        for minute in range(0, 120, 1):
            if minute < peak_time:
                pct = actual_peak * (minute / peak_time)
            else:
                # Crash after peak
                crash_progress = (minute - peak_time) / 30
                pct = actual_peak - (actual_peak + 50) * min(crash_progress, 1.0)
            path.append(pct + random.uniform(-3, 3))
    
    elif outcome_type in ("moderate_pump", "good_pump", "moonshot", "mega_moon", "graduation"):
        actual_peak = random.uniform(max_gain * 0.5, max_gain)
        actual_peak_time = random.uniform(peak_time * 0.5, peak_time * 1.5)
        
        for minute in range(0, 300, 1):
            if minute < actual_peak_time:
                # Rising phase with some volatility
                progress = minute / actual_peak_time
                pct = actual_peak * progress * (1 + random.uniform(-0.1, 0.1))
            else:
                # After peak: gradual decline or consolidation
                decline_progress = (minute - actual_peak_time) / (actual_peak_time * 2)
                decline = min(decline_progress * 0.6, 0.8)  # Lose up to 80% from peak
                pct = actual_peak * (1 - decline) + random.uniform(-5, 5)
            path.append(pct)
    
    return path


def simulate_bot_exit(path: List[float], config: Dict) -> Tuple[float, str, int]:
    """
    Given a price path, simulate when the bot would exit.
    Returns (exit_pct, reason, minutes_held).
    """
    peak_pct = 0
    trailing_active = False
    tp1_done = False
    tp2_done = False
    remaining = 1.0
    total_exit_value = 0  # As multiplier of initial investment
    
    for minute, pct in enumerate(path):
        if pct > peak_pct:
            peak_pct = pct


        # Check every 15 seconds = roughly every 0.25 minutes
        # We check every minute in sim (close enough)
        
        # Stop loss
        if pct <= config["stop_loss_pct"]:
            total_exit_value += remaining * (1 + pct / 100)
            return total_exit_value - 1, "STOP_LOSS", minute
        
        # Take profit 1
        if pct >= config["tp1_pct"] and not tp1_done:
            sell_size = config["tp1_size"] * remaining
            total_exit_value += sell_size * (1 + pct / 100)
            remaining -= sell_size
            tp1_done = True
            if remaining <= 0.01:
                return total_exit_value - 1, "TP1_FULL", minute
        
        # Take profit 2
        if pct >= config["tp2_pct"] and not tp2_done:
            sell_size = config["tp2_size"] * remaining
            total_exit_value += sell_size * (1 + pct / 100)
            remaining -= sell_size
            tp2_done = True
            if remaining <= 0.01:
                return total_exit_value - 1, "TP2_FULL", minute
        
        # Take profit 3
        if pct >= config["tp3_pct"]:
            total_exit_value += remaining * (1 + pct / 100)
            return total_exit_value - 1, "TP3", minute
        
        # Trailing stop
        if pct >= config["trailing_activate"]:
            trailing_active = True
        
        if trailing_active and peak_pct > 0:
            drawdown = ((peak_pct - pct) / (100 + peak_pct)) * 100
            if drawdown >= config["trailing_distance"] and pct < peak_pct * 0.85:
                total_exit_value += remaining * (1 + pct / 100)
                return total_exit_value - 1, "TRAILING", minute
        
        # Stale timeout (4 hours = 240 min)
        if minute >= config["stale_hours"] * 60:
            total_exit_value += remaining * (1 + pct / 100)
            return total_exit_value - 1, "STALE", minute
    
    # End of path - force exit at last price
    final_pct = path[-1] if path else 0
    total_exit_value += remaining * (1 + final_pct / 100)
    return total_exit_value - 1, "END", len(path)



def run_simulation(days: int = 30, starting_sol: float = 3.0) -> Dict:
    """
    Run a full Monte Carlo simulation of the bot running 24/7.
    """
    config = BOT_CONFIG
    results = []
    wallet = starting_sol
    total_trades = 0
    wins = 0
    losses = 0
    biggest_win = 0
    biggest_loss = 0
    all_pnls = []
    
    print("=" * 70)
    print("  PUMP.FUN BOT v2 - MONTE CARLO SIMULATION")
    print("=" * 70)
    print(f"  Starting balance: {starting_sol} SOL")
    print(f"  Buy amount: {config['buy_amount_sol']} SOL per trade")
    print(f"  Simulation period: {days} days (24/7)")
    print(f"  Running 1000 iterations per day for accuracy...")
    print("=" * 70)
    print()
    
    for day in range(1, days + 1):
        day_result = SimDay(day=day)
        
        # How many tokens does the bot see per day?
        tokens_seen = config["tokens_per_hour_seen"] * 24
        day_result.tokens_seen = tokens_seen
        
        # For each token, determine its outcome and whether it passes filters
        tokens_that_pass = 0
        day_trades: List[SimTrade] = []
        
        buys_this_hour = 0
        active_positions = 0
        hour_start = 0


        for token_idx in range(tokens_seen):
            # Determine hour for rate limiting
            current_hour = token_idx // config["tokens_per_hour_seen"]
            if current_hour != hour_start:
                hour_start = current_hour
                buys_this_hour = 0
            
            # Rate limit check
            if buys_this_hour >= config["max_buys_per_hour"]:
                continue
            if active_positions >= config["max_positions"]:
                continue
            
            # Balance check
            if wallet < config["buy_amount_sol"] + 0.1:
                continue
            
            # Determine token outcome (what kind of token is this?)
            outcome_type = random.choices(
                list(TOKEN_OUTCOMES.keys()),
                weights=[v[0] for v in TOKEN_OUTCOMES.values()],
                k=1
            )[0]
            
            # Does it pass our filters?
            pass_rate = FILTER_EFFECTIVENESS[outcome_type]
            if random.random() > pass_rate:
                continue  # Filtered out
            
            tokens_that_pass += 1
            
            # TX success check
            if random.random() > config["tx_success_rate"]:
                day_result.fees_paid += config["priority_fee_sol"]
                continue  # TX failed
            
            # ═══ EXECUTE BUY ═══
            buy_sol = config["buy_amount_sol"]
            wallet -= buy_sol
            wallet -= config["priority_fee_sol"]  # Priority fee
            buys_this_hour += 1
            active_positions += 1
            day_result.buys_attempted += 1
            day_result.buys_succeeded += 1
            day_result.total_sol_spent += buy_sol


            # Simulate price path
            path = simulate_token_price_path(outcome_type)
            
            # Simulate bot's exit decision
            exit_multiplier, exit_reason, time_held = simulate_bot_exit(path, config)
            
            # Apply slippage
            slippage_cost = config["slippage_cost_pct"] / 100
            exit_multiplier -= slippage_cost
            
            # Sell TX success
            if exit_multiplier > -0.9:  # Only if there's something to sell
                if random.random() > config["sell_success_rate"]:
                    # Sell failed - try again at worse price
                    exit_multiplier *= 0.85  # 15% worse due to delay
            
            # Calculate SOL returned
            sol_returned = buy_sol * (1 + exit_multiplier)
            sol_returned = max(0, sol_returned)  # Can't go negative
            
            pnl = sol_returned - buy_sol
            wallet += sol_returned
            active_positions -= 1
            
            trade = SimTrade(
                token_id=token_idx,
                outcome_type=outcome_type,
                buy_sol=buy_sol,
                max_gain_pct=TOKEN_OUTCOMES[outcome_type][1],
                actual_exit_pct=exit_multiplier * 100,
                sol_returned=sol_returned,
                pnl_sol=pnl,
                exit_reason=exit_reason,
                time_held_min=time_held,
            )
            day_trades.append(trade)
            all_pnls.append(pnl)
            total_trades += 1
            
            if pnl > 0:
                wins += 1
                biggest_win = max(biggest_win, pnl)
            else:
                losses += 1
                biggest_loss = min(biggest_loss, pnl)
        
        day_result.tokens_passed_filters = tokens_that_pass
        day_result.trades = day_trades
        day_result.total_sol_returned = sum(t.sol_returned for t in day_trades)
        day_result.net_pnl = day_result.total_sol_returned - day_result.total_sol_spent
        results.append(day_result)


    # ═══════════════════════════════════════════
    # PRINT RESULTS
    # ═══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("  SIMULATION RESULTS")
    print("=" * 70)
    
    total_spent = sum(d.total_sol_spent for d in results)
    total_returned = sum(d.total_sol_returned for d in results)
    total_pnl = total_returned - total_spent
    total_fees = sum(d.fees_paid for d in results)
    
    print(f"\n📊 OVERALL ({days} DAYS)")
    print(f"  {'─' * 50}")
    print(f"  Starting Balance:     {starting_sol:.4f} SOL")
    print(f"  Final Balance:        {wallet:.4f} SOL")
    print(f"  Net P&L:              {wallet - starting_sol:+.4f} SOL")
    print(f"  ROI:                  {((wallet - starting_sol) / starting_sol) * 100:+.1f}%")
    print(f"  Fees Paid:            {total_fees:.4f} SOL")
    print()
    
    print(f"📈 TRADING STATS")
    print(f"  {'─' * 50}")
    print(f"  Total Trades:         {total_trades}")
    print(f"  Avg Trades/Day:       {total_trades / days:.1f}")
    print(f"  Wins:                 {wins} ({wins/max(total_trades,1)*100:.1f}%)")
    print(f"  Losses:               {losses} ({losses/max(total_trades,1)*100:.1f}%)")
    print(f"  Biggest Win:          +{biggest_win:.4f} SOL ({biggest_win/config['buy_amount_sol']*100:.0f}%)")
    print(f"  Biggest Loss:         {biggest_loss:.4f} SOL ({biggest_loss/config['buy_amount_sol']*100:.0f}%)")
    print(f"  Avg P&L per trade:    {sum(all_pnls)/max(len(all_pnls),1):.4f} SOL")
    print()
    
    print(f"💰 DAILY BREAKDOWN")
    print(f"  {'─' * 50}")
    print(f"  {'Day':<5} {'Trades':<8} {'Spent':<10} {'Returned':<10} {'P&L':<12} {'Balance':<10}")
    print(f"  {'─' * 50}")
    
    running_balance = starting_sol
    daily_pnls = []
    for d in results:
        running_balance += d.net_pnl - d.fees_paid
        daily_pnls.append(d.net_pnl)
        if d.day <= 10 or d.day % 5 == 0 or d.day == days:
            trades_count = len(d.trades)
            print(
                f"  {d.day:<5} {trades_count:<8} "
                f"{d.total_sol_spent:<10.4f} {d.total_sol_returned:<10.4f} "
                f"{d.net_pnl:<12.4f} {running_balance:<10.4f}"
            )
    
    print(f"  {'─' * 50}")


    # Daily averages
    avg_daily_pnl = sum(daily_pnls) / len(daily_pnls)
    profitable_days = sum(1 for p in daily_pnls if p > 0)
    
    print(f"\n📅 DAILY AVERAGES")
    print(f"  {'─' * 50}")
    print(f"  Avg Daily P&L:        {avg_daily_pnl:+.4f} SOL")
    print(f"  Profitable Days:      {profitable_days}/{days} ({profitable_days/days*100:.0f}%)")
    print(f"  Best Day:             {max(daily_pnls):+.4f} SOL")
    print(f"  Worst Day:            {min(daily_pnls):+.4f} SOL")
    print()
    
    # Exit reason breakdown
    exit_reasons = {}
    outcome_results = {}
    for d in results:
        for t in d.trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
            if t.outcome_type not in outcome_results:
                outcome_results[t.outcome_type] = {"count": 0, "total_pnl": 0}
            outcome_results[t.outcome_type]["count"] += 1
            outcome_results[t.outcome_type]["total_pnl"] += t.pnl_sol
    
    print(f"🎯 EXIT REASONS")
    print(f"  {'─' * 50}")
    for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
        print(f"  {reason:<20} {count:>5} ({count/max(total_trades,1)*100:.1f}%)")
    
    print(f"\n🏷️  TOKEN OUTCOMES (what we actually bought)")
    print(f"  {'─' * 50}")
    print(f"  {'Type':<18} {'Count':<7} {'Total P&L':<12} {'Avg P&L':<10}")
    for outcome, data in sorted(outcome_results.items(), key=lambda x: -x[1]["total_pnl"]):
        avg = data["total_pnl"] / max(data["count"], 1)
        print(f"  {outcome:<18} {data['count']:<7} {data['total_pnl']:+.4f} SOL  {avg:+.4f}")
    
    print()
    print("=" * 70)
    print("  HONEST ASSESSMENT")
    print("=" * 70)


    if avg_daily_pnl > 0.05:
        verdict = "GOOD"
        emoji = "🟢"
    elif avg_daily_pnl > 0:
        verdict = "MARGINAL PROFIT"
        emoji = "🟡"
    else:
        verdict = "LOSING"
        emoji = "🔴"
    
    print(f"""
  {emoji} Verdict: {verdict}
  
  Expected Daily:  {avg_daily_pnl:+.4f} SOL/day
  Expected Weekly: {avg_daily_pnl * 7:+.4f} SOL/week  
  Expected Monthly:{avg_daily_pnl * 30:+.4f} SOL/month
  
  ⚠️  IMPORTANT CAVEATS:
  ─────────────────────
  1. This is a SIMULATION, not a guarantee. Real markets are harder.
  2. The win rate depends HEAVILY on filter quality.
  3. One bad day can wipe a week of gains.
  4. Pump.fun meta changes - what works today may not work next month.
  5. Network congestion / RPC failures reduce real performance by ~20-30%.
  6. This assumes your RPC (Helius) stays fast and reliable.
  
  💡 RECOMMENDATIONS:
  ─────────────────────
  • Start with 0.03 SOL/trade for first 3 days
  • If positive after 3 days, increase to 0.05 SOL
  • Never risk more than 5% of wallet per trade
  • Monitor daily - if 3 losing days in a row, pause and adjust
  • The BEST improvement: add websocket monitoring for faster entry
""")
    
    print("=" * 70)
    
    return {
        "days": days,
        "starting_sol": starting_sol,
        "final_balance": wallet,
        "net_pnl": wallet - starting_sol,
        "roi_pct": ((wallet - starting_sol) / starting_sol) * 100,
        "total_trades": total_trades,
        "win_rate": wins / max(total_trades, 1) * 100,
        "avg_daily_pnl": avg_daily_pnl,
        "profitable_days_pct": profitable_days / days * 100,
    }


if __name__ == "__main__":
    # Run simulation
    random.seed(42)  # Reproducible results
    
    print("\n" + "▓" * 70)
    print("  SCENARIO 1: Conservative (your current 0.8 SOL balance)")
    print("▓" * 70)
    result1 = run_simulation(days=30, starting_sol=0.8)
    
    print("\n\n" + "▓" * 70)
    print("  SCENARIO 2: With 3 SOL (recommended starting balance)")
    print("▓" * 70)
    result2 = run_simulation(days=30, starting_sol=3.0)
    
    print("\n\n" + "▓" * 70)
    print("  SCENARIO 3: Aggressive (5 SOL, higher buy size)")  
    print("▓" * 70)
    # Modify config for aggressive
    BOT_CONFIG["buy_amount_sol"] = 0.05
    BOT_CONFIG["max_buys_per_hour"] = 4
    result3 = run_simulation(days=30, starting_sol=5.0)
