"""
Token Scanner - Discovers new pump.fun tokens and enriches data.
PRIMARY: PumpPortal WebSocket (real-time, ~2600 tokens/hour, names included)
SECONDARY: DexScreener API (for enrichment + backup discovery)
"""

import asyncio
import time
import json
import logging
from typing import Dict, List, Optional, Callable

import httpx

import config
from token_resolver import TokenResolver

logger = logging.getLogger(__name__)


class TokenScanner:
    """
    Scans for new pump.fun tokens using:
    1. PumpPortal WebSocket (PRIMARY) - real-time new token stream
    2. DexScreener latest profiles (BACKUP) - polling every 15s
    
    PumpPortal gives us instant notifications with name/symbol already included.
    No more "?" names, no more missing tokens.
    """

    def __init__(self, resolver: TokenResolver):
        self.resolver = resolver
        self._seen_mints: set = set()
        self._on_new_token: Optional[Callable] = None
        self._running = False
        self._ws_connected = False
        self._ws_reconnect_delay = 5
        self._tokens_from_ws = 0
        self._tokens_from_dex = 0

    def on_new_token(self, callback: Callable):
        """Register callback for new token discoveries."""
        self._on_new_token = callback

    async def start(self):
        """Start all scanning methods concurrently."""
        self._running = True
        logger.info("Token scanner starting (PumpPortal WS + DexScreener backup)")

        # Run WebSocket stream and DexScreener polling concurrently
        tasks = [
            asyncio.create_task(self._run_pumpportal_ws()),
            asyncio.create_task(self._run_dexscreener_polling()),
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self):
        """Stop scanning."""
        self._running = False

    # ═══════════════════════════════════════════════════════════════
    # PRIMARY: PumpPortal WebSocket (real-time new token stream)
    # ═══════════════════════════════════════════════════════════════

    async def _run_pumpportal_ws(self):
        """
        Connect to PumpPortal WebSocket for real-time new token events.
        This is FREE and gives us instant notifications with full metadata.
        ~2600 new tokens per hour.
        """
        import websockets

        while self._running:
            try:
                logger.info("Connecting to PumpPortal WebSocket...")
                async with websockets.connect(
                    "wss://pumpportal.fun/api/data",
                    ping_interval=20,
                    ping_timeout=30,
                    close_timeout=10,
                ) as ws:
                    # Subscribe to new token creation events
                    await ws.send(json.dumps({
                        "method": "subscribeNewToken",
                    }))

                    self._ws_connected = True
                    self._ws_reconnect_delay = 5  # Reset on success
                    logger.info("PumpPortal WebSocket connected! Streaming new tokens...")

                    while self._running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
                            data = json.loads(msg)

                            # Parse the new token event
                            if data.get("mint"):
                                token = self._parse_pumpportal_event(data)
                                if token:
                                    mint = token["mint"]
                                    if mint not in self._seen_mints:
                                        self._seen_mints.add(mint)
                                        self._tokens_from_ws += 1
                                        if self._on_new_token:
                                            await self._on_new_token(token)

                        except asyncio.TimeoutError:
                            # Send ping to keep alive
                            try:
                                await ws.ping()
                            except:
                                break
                        except Exception as e:
                            logger.warning(f"WS message error: {e}")
                            break

            except Exception as e:
                self._ws_connected = False
                logger.warning(f"PumpPortal WS disconnected: {e}")
                logger.info(f"Reconnecting in {self._ws_reconnect_delay}s...")
                await asyncio.sleep(self._ws_reconnect_delay)
                self._ws_reconnect_delay = min(self._ws_reconnect_delay * 1.5, 60)

    def _parse_pumpportal_event(self, data: Dict) -> Optional[Dict]:
        """Parse a PumpPortal new token WebSocket event."""
        mint = data.get("mint", "")
        if not mint:
            return None

        name = data.get("name", "")
        symbol = data.get("symbol", "")

        # Skip tokens without names (very rare from PumpPortal)
        if not name or not symbol:
            return None

        # Get initial market cap from bonding curve data
        # PumpPortal sends vSolInBondingCurve or marketCapSol
        mc_sol = data.get("marketCapSol", data.get("vSolInBondingCurve", 0))
        sol_price_usd = 170  # Will be updated during enrichment
        mc_usd = mc_sol * sol_price_usd if mc_sol else 0

        return {
            "mint": mint,
            "name": name,
            "symbol": symbol,
            "creator": data.get("traderPublicKey", ""),
            "image": data.get("uri", ""),
            "twitter": data.get("twitter", ""),
            "telegram": data.get("telegram", ""),
            "website": data.get("website", ""),
            "complete": False,  # New tokens are never graduated
            "market_cap_usd": mc_usd,
            "market_cap_sol": mc_sol,
            "initial_buy_sol": data.get("initialBuy", 0),
            "source": "pumpportal_ws",
            "discovered_at": time.time(),
        }

    # ═══════════════════════════════════════════════════════════════
    # SECONDARY: DexScreener Polling (backup + trending tokens)
    # ═══════════════════════════════════════════════════════════════

    async def _run_dexscreener_polling(self):
        """Backup: Poll DexScreener every 15s for tokens that gained traction."""
        while self._running:
            try:
                tokens = await self._scan_dexscreener_new()
                for token in tokens:
                    mint = token.get("mint", "")
                    if mint and mint not in self._seen_mints:
                        self._seen_mints.add(mint)
                        self._tokens_from_dex += 1
                        if self._on_new_token:
                            await self._on_new_token(token)
            except Exception as e:
                logger.debug(f"DexScreener poll error: {e}")

            await asyncio.sleep(15)  # Every 15 seconds

    async def _scan_dexscreener_new(self) -> List[Dict]:
        """Scan DexScreener for newest Solana pairs."""
        tokens = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Latest token profiles
                resp = await client.get(
                    "https://api.dexscreener.com/token-profiles/latest/v1",
                    params={"chainId": "solana"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        for item in data[:30]:
                            token = self._parse_dexscreener_profile(item)
                            if token:
                                tokens.append(token)

                # Also check latest boosted (trending)
                resp2 = await client.get(
                    "https://api.dexscreener.com/token-boosts/latest/v1"
                )
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    if isinstance(data2, list):
                        for item in data2[:20]:
                            if item.get("chainId") == "solana":
                                token = self._parse_dexscreener_profile(item)
                                if token:
                                    tokens.append(token)

        except Exception as e:
            logger.debug(f"DexScreener scan error: {e}")

        return tokens

    def _parse_dexscreener_profile(self, item: Dict) -> Optional[Dict]:
        """Parse a DexScreener token profile into our format."""
        mint = item.get("tokenAddress", "")
        if not mint:
            return None

        chain = item.get("chainId", "")
        if chain != "solana":
            return None

        return {
            "mint": mint,
            "source": "dexscreener",
            "discovered_at": time.time(),
        }

    # ═══════════════════════════════════════════════════════════════
    # ENRICHMENT (called by bot.py after basic filters)
    # ═══════════════════════════════════════════════════════════════

    async def enrich_token(self, token: Dict) -> Dict:
        """
        Enrich a token with full market data from DexScreener + pump.fun.
        Called after a token passes basic filters.
        """
        mint = token.get("mint", "")
        if not mint:
            return token

        # 1. Resolve name/symbol if missing (DexScreener tokens may not have it)
        if not token.get("name") or token["name"] in ("", "?", "Unknown"):
            metadata = await self.resolver.resolve(mint)
            token.update({
                "name": metadata.get("name", token.get("name", "")),
                "symbol": metadata.get("symbol", token.get("symbol", "")),
                "image": metadata.get("image", token.get("image", "")),
                "twitter": metadata.get("twitter", token.get("twitter", "")),
                "telegram": metadata.get("telegram", token.get("telegram", "")),
                "website": metadata.get("website", token.get("website", "")),
                "creator": metadata.get("creator", token.get("creator", "")),
            })

        # 2. Get DexScreener market data (MC, volume, liquidity)
        dex_data = await self._get_dexscreener_data(mint)
        if dex_data:
            token.update(dex_data)

        # 3. Get pump.fun bonding curve data (if DexScreener didn't have MC)
        if not token.get("market_cap_usd") or token["market_cap_usd"] == 0:
            pump_data = await self.resolver.get_full_pumpfun_data(mint)
            if pump_data:
                token["complete"] = pump_data.get("complete", False)
                token["virtual_sol_reserves"] = pump_data.get("virtual_sol_reserves", 0)
                token["virtual_token_reserves"] = pump_data.get("virtual_token_reserves", 0)
                if not token.get("creator"):
                    token["creator"] = pump_data.get("creator", "")
                if not token.get("twitter"):
                    token["twitter"] = pump_data.get("twitter", "")
                if not token.get("telegram"):
                    token["telegram"] = pump_data.get("telegram", "")
                if not token.get("website"):
                    token["website"] = pump_data.get("website", "")

                # Calculate MC from bonding curve
                vs = pump_data.get("virtual_sol_reserves", 0)
                vt = pump_data.get("virtual_token_reserves", 0)
                ts = pump_data.get("total_supply", 1_000_000_000 * 1e6)
                if vs and vt and vt > 0:
                    sol_price_usd = 170
                    price = (vs / 1e9) / (vt / 1e6)
                    token["market_cap_usd"] = price * (ts / 1e6) * sol_price_usd

        return token

    async def _get_dexscreener_data(self, mint: str) -> Optional[Dict]:
        """Get market data from DexScreener."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    pairs = data.get("pairs") or []
                    if not pairs:
                        return None

                    # Use the most liquid pair
                    pair = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))

                    price_change = pair.get("priceChange", {})
                    txns = pair.get("txns", {})
                    txns_5m = txns.get("m5", {})
                    txns_1h = txns.get("h1", {})

                    return {
                        "market_cap_usd": float(pair.get("marketCap", 0) or 0),
                        "liquidity_usd": float(pair.get("liquidity", {}).get("usd", 0) or 0),
                        "volume_5m_usd": float(pair.get("volume", {}).get("m5", 0) or 0),
                        "volume_1h_usd": float(pair.get("volume", {}).get("h1", 0) or 0),
                        "price_change_5m": float(price_change.get("m5", 0) or 0),
                        "price_change_1h": float(price_change.get("h1", 0) or 0),
                        "buys_5m": int(txns_5m.get("buys", 0) or 0),
                        "sells_5m": int(txns_5m.get("sells", 0) or 0),
                        "buys_1h": int(txns_1h.get("buys", 0) or 0),
                        "sells_1h": int(txns_1h.get("sells", 0) or 0),
                        "pair_created_at": pair.get("pairCreatedAt", 0),
                        "dex_id": pair.get("dexId", ""),
                    }
        except Exception as e:
            logger.debug(f"DexScreener data fetch error for {mint}: {e}")

        return None

    def mark_seen(self, mint: str):
        """Mark a token as already seen/evaluated."""
        self._seen_mints.add(mint)

    def seen_count(self) -> int:
        return len(self._seen_mints)

    def get_stats(self) -> Dict:
        """Get scanner statistics."""
        return {
            "ws_connected": self._ws_connected,
            "tokens_from_ws": self._tokens_from_ws,
            "tokens_from_dex": self._tokens_from_dex,
            "total_seen": len(self._seen_mints),
        }
