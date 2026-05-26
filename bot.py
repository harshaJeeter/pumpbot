"""
Pump.fun Trading Bot - Main Entry Point
Orchestrates scanning, scoring, trading, and position management.
"""

import asyncio
import time
import logging
import signal
import sys

import config
from token_resolver import TokenResolver
from rug_detector import RugDetector
from scoring_engine import ScoringEngine
from scanner import TokenScanner
from trader import Trader
from position_manager import PositionManager
from data_store import DataStore
from notifier import TelegramNotifier
from dashboard import create_dashboard_app

# ═══════════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", mode="a"),
    ]
)
logger = logging.getLogger("pumpbot")


class PumpBot:
    """
    Main bot orchestrator.
    Flow: Scan → Enrich → Score → Rug Check → Buy → Monitor → Sell
    """

    def __init__(self):
        # Core components
        self.resolver = TokenResolver(config.HELIUS_RPC_URL, config.HELIUS_API_KEY)
        self.rug_detector = RugDetector()
        self.scoring_engine = ScoringEngine()
        self.scanner = TokenScanner(self.resolver)
        self.trader = Trader()
        self.position_manager = PositionManager(self.trader)
        self.data_store = DataStore()
        self.notifier = TelegramNotifier()

        # State
        self._running = False
        self._start_time = time.time()
        self._tokens_evaluated = 0
        self._tokens_passed = 0
        self._tokens_rejected = 0

        # Register scanner callback
        self.scanner.on_new_token(self._on_token_discovered)

    async def start(self):
        """Start the bot."""
        self._running = True
        logger.info("=" * 60)
        logger.info("  PUMP.FUN TRADING BOT - STARTING")
        logger.info("=" * 60)

        # Check wallet
        balance = await self.trader.get_sol_balance()
        logger.info(f"Wallet: {self.trader.wallet_pubkey}")
        logger.info(f"Balance: {balance:.4f} SOL")
        logger.info(f"Buy amount: {config.BUY_AMOUNT_SOL} SOL per trade")
        logger.info(f"Min score: {config.MIN_SCORE}")
        logger.info(f"MC range: ${config.MIN_MC_USD:,} - ${config.MAX_MC_USD:,}")
        logger.info("=" * 60)

        if balance < config.MIN_SOL_BALANCE:
            logger.error(f"Insufficient balance! Need at least {config.MIN_SOL_BALANCE} SOL")
            await self.notifier.notify_alert(f"Low balance: {balance:.4f} SOL")

        # Start components concurrently
        tasks = [
            asyncio.create_task(self.scanner.start()),
            asyncio.create_task(self.position_manager.start_monitoring()),
            asyncio.create_task(self._periodic_cleanup()),
            asyncio.create_task(self._run_dashboard()),
        ]

        await self.notifier.notify_alert(
            f"Bot started! Balance: {balance:.4f} SOL, "
            f"Active positions: {self.position_manager.get_active_count()}"
        )

        # Wait for all tasks
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Bot shutting down...")
        finally:
            self._running = False

    async def stop(self):
        """Stop the bot gracefully."""
        logger.info("Stopping bot...")
        self._running = False
        self.scanner.stop()
        self.position_manager.stop()
        await self.notifier.notify_alert("Bot stopped!")

    async def _on_token_discovered(self, token: Dict):
        """Callback when scanner finds a new token."""
        mint = token.get("mint", "")
        if not mint:
            return

        # ═══ DEDUP CHECK ═══
        if self.data_store.has_bought(mint):
            return  # Already bought this token
        if self.data_store.has_seen(mint):
            return  # Already evaluated
        if self.data_store.is_blocked(mint):
            return  # Blocklisted

        self.data_store.mark_seen(mint)
        self._tokens_evaluated += 1

        # ═══ POSITION LIMIT CHECK ═══
        if self.position_manager.get_active_count() >= config.MAX_ACTIVE_POSITIONS:
            return  # Too many open positions

        # ═══ RATE LIMIT CHECK ═══
        can_buy, reason = self.trader.can_buy()
        if not can_buy:
            return

        # ═══ ENRICH TOKEN DATA ═══
        try:
            token = await self.scanner.enrich_token(token)
        except Exception as e:
            logger.debug(f"Enrich failed for {mint[:8]}...: {e}")
            return

        # ═══ BASIC FILTERS ═══
        if not self._passes_basic_filters(token):
            self._tokens_rejected += 1
            return

        # ═══ RUG DETECTION ═══
        rug_score, rug_flags = self.rug_detector.analyze(token)
        if rug_score > 50:
            logger.info(
                f"RUG REJECTED: {token.get('symbol', '?')} "
                f"(risk={rug_score}, flags={rug_flags})"
            )
            self._tokens_rejected += 1
            # Auto-blocklist high-risk tokens
            if rug_score > 75:
                self.data_store.block_token(mint, f"rug_score={rug_score}")
                creator = token.get("creator", "")
                if creator:
                    self.data_store.block_creator(creator, "high_risk_creator")
            return

        # ═══ SCORING ═══
        score, breakdown = self.scoring_engine.score(token)
        if score < config.MIN_SCORE:
            logger.debug(
                f"LOW SCORE: {token.get('symbol', '?')} = {score} "
                f"(need {config.MIN_SCORE}) [{breakdown}]"
            )
            self._tokens_rejected += 1
            return

        # ═══ PASSED ALL CHECKS - EXECUTE BUY ═══
        self._tokens_passed += 1
        await self._execute_buy(token, score, breakdown)

    def _passes_basic_filters(self, token: Dict) -> bool:
        """Quick pre-filters before expensive scoring."""
        # Name must be resolved (not "?")
        name = token.get("name", "")
        symbol = token.get("symbol", "")
        if not name or name in ("?", "Unknown", ""):
            return False
        if not symbol or symbol in ("?", "???", ""):
            return False

        # Already graduated = can't buy on pump.fun
        if token.get("complete"):
            return False

        # Market cap range (check if available - WS tokens may not have MC yet)
        mc = token.get("market_cap_usd", 0)
        if mc > 0:
            if mc < config.MIN_MC_USD or mc > config.MAX_MC_USD:
                return False
        else:
            # For PumpPortal WS tokens, check market_cap_sol instead
            mc_sol = token.get("market_cap_sol", 0)
            if mc_sol > 0:
                # ~28 SOL initial = ~$4,760 at $170/SOL
                # Min ~47 SOL ($8K), Max ~470 SOL ($80K)
                min_sol = config.MIN_MC_USD / 170
                max_sol = config.MAX_MC_USD / 170
                if mc_sol < min_sol or mc_sol > max_sol:
                    return False

        # Minimum liquidity (only check if available - new tokens won't have it)
        liq = token.get("liquidity_usd", 0)
        if liq > 0 and liq < config.MIN_LIQUIDITY_USD:
            return False

        # Token age check (from DexScreener pair creation time)
        created_at = token.get("pair_created_at", 0)
        if created_at:
            age_seconds = time.time() - (created_at / 1000)  # DexScreener uses ms
            if age_seconds < config.MIN_TOKEN_AGE_SECONDS:
                return False  # Too new, might be instant rug
            if age_seconds > config.MAX_TOKEN_AGE_SECONDS:
                return False  # Too old, missed the early pump
        else:
            # For WS tokens, check discovered_at
            discovered_at = token.get("discovered_at", 0)
            if discovered_at:
                age = time.time() - discovered_at
                if age < config.MIN_TOKEN_AGE_SECONDS:
                    return False

        # Creator blocklist
        creator = token.get("creator", "")
        if creator and self.data_store.is_creator_blocked(creator):
            return False

        return True

    async def _execute_buy(self, token: Dict, score: int, breakdown: Dict):
        """Execute a buy trade."""
        mint = token.get("mint", "")
        symbol = token.get("symbol", "???")
        name = token.get("name", "Unknown")
        mc = token.get("market_cap_usd", 0)

        logger.info(
            f"🟢 BUYING: {symbol} ({name}) | "
            f"Score={score} | MC=${mc:,.0f} | "
            f"Breakdown={breakdown}"
        )

        # Execute buy
        result = await self.trader.buy_token(mint, config.BUY_AMOUNT_SOL)

        if result["success"]:
            tx_sig = result["tx_signature"]
            logger.info(f"✅ BUY SUCCESS: {symbol} | TX: {tx_sig}")

            # Get token amount received (estimate based on MC)
            # Real amount comes from parsing the transaction
            token_amount = await self._estimate_tokens_received(mint, config.BUY_AMOUNT_SOL)

            # Add position
            self.position_manager.add_position(
                mint=mint,
                name=name,
                symbol=symbol,
                buy_mc_usd=mc,
                buy_amount_sol=config.BUY_AMOUNT_SOL,
                token_amount=token_amount,
                buy_tx=tx_sig,
            )

            # Log trade
            self.data_store.log_trade(
                action="buy", mint=mint, symbol=symbol, name=name,
                success=True, sol_amount=config.BUY_AMOUNT_SOL,
                mc_usd=mc, score=score, tx_sig=tx_sig,
                method=result.get("method", ""),
            )

            # Notify
            await self.notifier.notify_buy(
                symbol=symbol, name=name, mint=mint,
                sol_amount=config.BUY_AMOUNT_SOL, mc_usd=mc,
                score=score, tx_sig=tx_sig,
            )
        else:
            error = result.get("error", "Unknown error")
            logger.warning(f"❌ BUY FAILED: {symbol} | {error}")

            self.data_store.log_trade(
                action="buy", mint=mint, symbol=symbol, name=name,
                success=False, sol_amount=config.BUY_AMOUNT_SOL,
                mc_usd=mc, score=score, error=error,
            )

    async def _estimate_tokens_received(self, mint: str, sol_amount: float) -> int:
        """Estimate token amount from a buy (will be corrected on sell)."""
        # Check actual balance
        balance = await self.trader.get_token_balance(mint)
        if balance > 0:
            return balance

        # Fallback: estimate from bonding curve
        pump_data = await self.resolver.get_full_pumpfun_data(mint)
        if pump_data:
            sol_reserves = pump_data.get("virtual_sol_reserves", 0) / 1e9
            token_reserves = pump_data.get("virtual_token_reserves", 0) / 1e6
            if sol_reserves > 0:
                # Simple estimate: amount * (token_reserves / sol_reserves)
                estimated = int(sol_amount * (token_reserves / sol_reserves) * 1e6)
                return estimated

        return 0

    async def _periodic_cleanup(self):
        """Periodic maintenance tasks."""
        while self._running:
            await asyncio.sleep(300)  # Every 5 minutes
            try:
                self.scoring_engine.cleanup_old_data()
                logger.debug(
                    f"Stats: evaluated={self._tokens_evaluated}, "
                    f"passed={self._tokens_passed}, "
                    f"rejected={self._tokens_rejected}, "
                    f"positions={self.position_manager.get_active_count()}"
                )
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

    async def _run_dashboard(self):
        """Run the web dashboard in background."""
        try:
            from dashboard import run_dashboard
            # Run in a thread since Flask is synchronous
            import threading
            thread = threading.Thread(
                target=run_dashboard,
                args=(self,),
                daemon=True,
            )
            thread.start()
            logger.info(f"Dashboard running on port {config.DASHBOARD_PORT}")
        except Exception as e:
            logger.error(f"Dashboard failed to start: {e}")

        # Keep this coroutine alive
        while self._running:
            await asyncio.sleep(60)

    def get_status(self) -> Dict:
        """Get bot status for dashboard."""
        uptime = time.time() - self._start_time
        return {
            "running": self._running,
            "uptime_hours": uptime / 3600,
            "wallet": self.trader.wallet_pubkey,
            "tokens_evaluated": self._tokens_evaluated,
            "tokens_passed": self._tokens_passed,
            "tokens_rejected": self._tokens_rejected,
            "pass_rate": self._tokens_passed / max(self._tokens_evaluated, 1) * 100,
            "active_positions": self.position_manager.get_active_count(),
            "scanner_seen": self.scanner.seen_count(),
        }


# Need this import for type hints in callback
from typing import Dict


def main():
    """Entry point."""
    bot = PumpBot()

    # Handle shutdown signals
    loop = asyncio.new_event_loop()

    def shutdown(sig):
        logger.info(f"Received signal {sig}")
        loop.create_task(bot.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: shutdown(s))

    try:
        loop.run_until_complete(bot.start())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
        loop.run_until_complete(bot.stop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
