"""
Token Scanner - Discovers new pump.fun tokens and enriches data.
Uses DexScreener + pump.fun API for real-time token discovery.
"""

import asyncio
import time
import logging
from typing import Dict, List, Optional, Callable

import httpx

import config
from token_resolver import TokenResolver

logger = logging.getLogger(__name__)


class TokenScanner:
    """
    Scans for new pump.fun tokens using multiple methods:
    1. DexScreener latest pairs (Solana, pump.fun DEX)
    2. Pump.fun frontend API new coins
    3. DexScreener boosted/trending
    
    Enriches each token with full metadata before passing to evaluator.
    """

    def __init__(self, resolver: TokenResolver):
        self.resolver = resolver
        self._seen_mints: set = set()
        self._on_new_token: Optional[Callable] = None
        self._running = False
        self._scan_interval = 8  # seconds between scans

    def on_new_token(self, callback: Callable):
        """Register callback for new token discoveries."""
        self._on_new_token = callback

    async def start(self):
        """Start scanning loop."""
        self._running = True
        logger.info("Token scanner started")

        while self._running:
            try:
                tokens = await self._scan_all_sources()
                for token in tokens:
                    mint = token.get("mint", "")
                    if mint and mint not in self._seen_mints:
                        self._seen_mints.add(mint)
                        if self._on_new_token:
                            await self._on_new_token(token)
            except Exception as e:
                logger.error(f"Scanner error: {e}")

            await asyncio.sleep(self._scan_interval)

    def stop(self):
        """Stop scanning."""
        self._running = False

    async def _scan_all_sources(self) -> List[Dict]:
        """Scan all token sources and merge results."""
        results = []

        # Run sources concurrently
        tasks = [
            self._scan_dexscreener_new(),
            self._scan_pumpfun_latest(),
        ]

        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        for result in gathered:
            if isinstance(result, list):
                results.extend(result)
            elif isinstance(result, Exception):
                logger.debug(f"Source scan failed: {result}")

        return results

    async def _scan_dexscreener_new(self) -> List[Dict]:
        """Scan DexScreener for newest Solana pairs on pump.fun."""
        tokens = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Get latest token profiles
                resp = await client.get(
                    "https://api.dexscreener.com/token-profiles/latest/v1",
                    params={"chainId": "solana"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        for item in data[:30]:  # Check latest 30
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

    async def _scan_pumpfun_latest(self) -> List[Dict]:
        """Scan pump.fun for latest coins."""
        tokens = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://frontend-api-v3.pump.fun/coins/latest",
                    params={"limit": 30, "includeNsfw": "false"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    coins = data if isinstance(data, list) else data.get("coins", [])
                    for coin in coins:
                        token = self._parse_pumpfun_coin(coin)
                        if token:
                            tokens.append(token)

                # Also check "king of the hill" (trending on pump.fun)
                resp2 = await client.get(
                    "https://frontend-api-v3.pump.fun/coins/king-of-the-hill",
                    params={"limit": 10, "includeNsfw": "false"}
                )
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    coins2 = data2 if isinstance(data2, list) else data2.get("coins", [])
                    for coin in coins2:
                        token = self._parse_pumpfun_coin(coin)
                        if token:
                            tokens.append(token)

        except Exception as e:
            logger.debug(f"Pump.fun scan error: {e}")

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

    def _parse_pumpfun_coin(self, coin: Dict) -> Optional[Dict]:
        """Parse pump.fun coin data into our format."""
        mint = coin.get("mint", "")
        if not mint:
            return None

        # Don't bother with graduated tokens (can't buy on pump.fun anymore)
        if coin.get("complete"):
            return None

        # Calculate market cap from virtual reserves
        virtual_sol = coin.get("virtual_sol_reserves", 0)
        virtual_token = coin.get("virtual_token_reserves", 0)
        total_supply = coin.get("total_supply", 1_000_000_000 * 1e6)  # default 1B tokens

        mc_usd = 0
        if virtual_sol and virtual_token and virtual_token > 0:
            # Price per token = sol_reserves / token_reserves
            # MC = price * total_supply
            sol_price_usd = 170  # Approximate, will be updated
            price_per_token = (virtual_sol / 1e9) / (virtual_token / 1e6)
            mc_usd = price_per_token * (total_supply / 1e6) * sol_price_usd

        return {
            "mint": mint,
            "name": coin.get("name", ""),
            "symbol": coin.get("symbol", ""),
            "image": coin.get("image_uri", ""),
            "twitter": coin.get("twitter", ""),
            "telegram": coin.get("telegram", ""),
            "website": coin.get("website", ""),
            "creator": coin.get("creator", ""),
            "complete": coin.get("complete", False),
            "virtual_sol_reserves": virtual_sol,
            "virtual_token_reserves": virtual_token,
            "total_supply": total_supply,
            "market_cap_usd": mc_usd,
            "source": "pumpfun",
            "discovered_at": time.time(),
        }

    async def enrich_token(self, token: Dict) -> Dict:
        """
        Enrich a token with full data from multiple APIs.
        This is called before scoring.
        """
        mint = token.get("mint", "")
        if not mint:
            return token

        # 1. Resolve name/symbol if missing
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

        # 2. Get DexScreener data for MC, volume, liquidity
        dex_data = await self._get_dexscreener_data(mint)
        if dex_data:
            token.update(dex_data)

        # 3. Get pump.fun specific data (bonding curve, holders)
        pump_data = await self.resolver.get_full_pumpfun_data(mint)
        if pump_data:
            token["complete"] = pump_data.get("complete", False)
            token["virtual_sol_reserves"] = pump_data.get("virtual_sol_reserves", 0)
            token["virtual_token_reserves"] = pump_data.get("virtual_token_reserves", 0)
            token["total_supply"] = pump_data.get("total_supply", 0)
            if not token.get("creator"):
                token["creator"] = pump_data.get("creator", "")
            if not token.get("twitter"):
                token["twitter"] = pump_data.get("twitter", "")
            if not token.get("telegram"):
                token["telegram"] = pump_data.get("telegram", "")
            if not token.get("website"):
                token["website"] = pump_data.get("website", "")

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
