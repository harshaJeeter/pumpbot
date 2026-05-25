"""
Notifier - Telegram notifications for trades and alerts.
"""

import asyncio
import logging
from typing import Optional

import httpx

import config

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends trade alerts to Telegram."""

    def __init__(self):
        self.enabled = config.TELEGRAM_ENABLED
        self.bot_token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID

    async def send(self, message: str, parse_mode: str = "HTML"):
        """Send a message to Telegram."""
        if not self.enabled:
            return

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": message,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    }
                )
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

    async def notify_buy(self, symbol: str, name: str, mint: str,
                         sol_amount: float, mc_usd: float, score: int,
                         tx_sig: str):
        """Send buy notification."""
        msg = (
            f"🟢 <b>BUY</b> — {symbol} ({name})\n"
            f"💰 Amount: {sol_amount} SOL\n"
            f"📊 MC: ${mc_usd:,.0f}\n"
            f"⭐ Score: {score}/100\n"
            f"🔗 <a href='https://pump.fun/{mint}'>Pump.fun</a> | "
            f"<a href='https://solscan.io/tx/{tx_sig}'>TX</a>"
        )
        await self.send(msg)

    async def notify_sell(self, symbol: str, reason: str, pnl_pct: float,
                          sol_received: float, tx_sig: str):
        """Send sell notification."""
        emoji = "🟢" if pnl_pct > 0 else "🔴"
        msg = (
            f"{emoji} <b>SELL</b> — {symbol}\n"
            f"📈 P&L: {pnl_pct:+.1f}%\n"
            f"💰 Received: {sol_received:.4f} SOL\n"
            f"📋 Reason: {reason}\n"
            f"🔗 <a href='https://solscan.io/tx/{tx_sig}'>TX</a>"
        )
        await self.send(msg)

    async def notify_alert(self, message: str):
        """Send a general alert."""
        await self.send(f"⚠️ {message}")

    async def notify_daily_summary(self, stats: dict):
        """Send daily P&L summary."""
        msg = (
            f"📊 <b>Daily Summary</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Trades: {stats.get('total_trades', 0)}\n"
            f"Active: {stats.get('active_positions', 0)}\n"
            f"Wins/Losses: {stats.get('wins', 0)}/{stats.get('losses', 0)}\n"
            f"Win Rate: {stats.get('win_rate', 0):.1f}%\n"
            f"Net P&L: {stats.get('net_pnl_sol', 0):+.4f} SOL\n"
            f"━━━━━━━━━━━━━━━"
        )
        await self.send(msg)
