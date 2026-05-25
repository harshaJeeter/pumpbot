"""
Data Store - Persistent storage for trade logs, blocklist, and seen tokens.
"""

import json
import os
import time
import logging
from typing import Dict, List, Set

import config

logger = logging.getLogger(__name__)


class DataStore:
    """Manages all persistent data files."""

    def __init__(self):
        os.makedirs(config.DATA_DIR, exist_ok=True)
        self._trade_log: List[Dict] = []
        self._blocklist: Set[str] = set()
        self._creator_blocklist: Set[str] = set()
        self._seen_tokens: Set[str] = set()
        self._bought_tokens: Set[str] = set()  # DEDUP: tokens we've already bought
        self._load_all()

    def _load_all(self):
        """Load all data files."""
        self._trade_log = self._load_json(config.TRADE_LOG_FILE, [])
        blocklist_data = self._load_json(config.BLOCKLIST_FILE, {"tokens": [], "creators": []})
        self._blocklist = set(blocklist_data.get("tokens", []))
        self._creator_blocklist = set(blocklist_data.get("creators", []))
        self._seen_tokens = set(self._load_json(config.SEEN_TOKENS_FILE, []))

        # Build bought tokens set from trade log
        for trade in self._trade_log:
            if trade.get("action") == "buy" and trade.get("success"):
                self._bought_tokens.add(trade.get("mint", ""))

    # ═══════════════════════════════════════════
    # TRADE LOG
    # ═══════════════════════════════════════════

    def log_trade(self, action: str, mint: str, symbol: str, name: str,
                  success: bool, sol_amount: float = 0, mc_usd: float = 0,
                  score: int = 0, tx_sig: str = "", error: str = "",
                  pnl_pct: float = 0, reason: str = "", method: str = ""):
        """Log a trade event."""
        entry = {
            "time": time.time(),
            "action": action,
            "mint": mint,
            "symbol": symbol,
            "name": name,
            "success": success,
            "sol_amount": sol_amount,
            "mc_usd": mc_usd,
            "score": score,
            "tx_signature": tx_sig,
            "error": error,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "method": method,
        }
        self._trade_log.append(entry)

        if action == "buy" and success:
            self._bought_tokens.add(mint)

        self._save_json(config.TRADE_LOG_FILE, self._trade_log)
        return entry

    def get_trade_log(self, limit: int = 100) -> List[Dict]:
        """Get recent trade log entries."""
        return self._trade_log[-limit:]

    def get_session_stats(self) -> Dict:
        """Get stats for current session."""
        buys = [t for t in self._trade_log if t["action"] == "buy"]
        sells = [t for t in self._trade_log if t["action"] == "sell"]

        successful_buys = [t for t in buys if t["success"]]
        successful_sells = [t for t in sells if t["success"]]
        failed_buys = [t for t in buys if not t["success"]]

        total_spent = sum(t.get("sol_amount", 0) for t in successful_buys)
        total_received = sum(t.get("sol_amount", 0) for t in successful_sells)

        return {
            "total_buys": len(buys),
            "successful_buys": len(successful_buys),
            "failed_buys": len(failed_buys),
            "buy_success_rate": len(successful_buys) / max(len(buys), 1) * 100,
            "total_sells": len(sells),
            "successful_sells": len(successful_sells),
            "total_sol_spent": total_spent,
            "total_sol_received": total_received,
            "net_pnl_sol": total_received - total_spent,
        }

    # ═══════════════════════════════════════════
    # DEDUPLICATION
    # ═══════════════════════════════════════════

    def has_bought(self, mint: str) -> bool:
        """Check if we've already bought this token (dedup)."""
        return mint in self._bought_tokens

    def mark_seen(self, mint: str):
        """Mark a token as seen/evaluated."""
        self._seen_tokens.add(mint)
        # Save periodically (every 50 tokens)
        if len(self._seen_tokens) % 50 == 0:
            self._save_json(config.SEEN_TOKENS_FILE, list(self._seen_tokens))

    def has_seen(self, mint: str) -> bool:
        """Check if we've already evaluated this token."""
        return mint in self._seen_tokens

    # ═══════════════════════════════════════════
    # BLOCKLIST
    # ═══════════════════════════════════════════

    def is_blocked(self, mint: str) -> bool:
        """Check if token is blocklisted."""
        return mint in self._blocklist

    def is_creator_blocked(self, creator: str) -> bool:
        """Check if creator is blocklisted."""
        return creator in self._creator_blocklist

    def block_token(self, mint: str, reason: str = ""):
        """Add token to blocklist."""
        self._blocklist.add(mint)
        self._save_blocklist()
        logger.info(f"Blocked token: {mint} ({reason})")

    def block_creator(self, creator: str, reason: str = ""):
        """Add creator to blocklist."""
        self._creator_blocklist.add(creator)
        self._save_blocklist()
        logger.info(f"Blocked creator: {creator} ({reason})")

    def _save_blocklist(self):
        data = {
            "tokens": list(self._blocklist),
            "creators": list(self._creator_blocklist),
        }
        self._save_json(config.BLOCKLIST_FILE, data)

    # ═══════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════

    def _load_json(self, path: str, default):
        """Load a JSON file with fallback."""
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load {path}: {e}")
        return default

    def _save_json(self, path: str, data):
        """Save data to JSON file."""
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save {path}: {e}")
