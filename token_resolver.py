"""
Token Resolver - Fixes the "?" name/symbol issue.
Multiple fallback methods to resolve token metadata.
"""

import httpx
import asyncio
import base64
import struct
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)


class TokenResolver:
    """
    Resolves token name/symbol using multiple sources:
    1. DexScreener API (most reliable for pump.fun tokens)
    2. Pump.fun frontend API
    3. Helius DAS API (Digital Asset Standard)
    4. On-chain Metaplex metadata
    """

    def __init__(self, helius_rpc_url: str, helius_api_key: str):
        self.helius_rpc_url = helius_rpc_url
        self.helius_api_key = helius_api_key
        self._cache: Dict[str, Dict] = {}  # mint -> {name, symbol, image, ...}

    async def resolve(self, mint: str) -> Dict:
        """
        Resolve token metadata. Returns dict with name, symbol, image, uri.
        Uses cache to avoid repeated lookups.
        """
        if mint in self._cache:
            return self._cache[mint]

        result = {"name": "Unknown", "symbol": "???", "mint": mint, "image": "", "uri": ""}

        # Try methods in order of reliability
        methods = [
            self._from_dexscreener,
            self._from_pumpfun_api,
            self._from_helius_das,
        ]

        for method in methods:
            try:
                data = await method(mint)
                if data and data.get("name") and data["name"] != "Unknown":
                    result.update(data)
                    break
            except Exception as e:
                logger.debug(f"Token resolve method {method.__name__} failed for {mint}: {e}")
                continue

        self._cache[mint] = result
        return result

    async def _from_dexscreener(self, mint: str) -> Optional[Dict]:
        """Fetch from DexScreener - works great for pump.fun tokens."""
        async with httpx.AsyncClient(timeout=10) as client:
            # Try token search
            resp = await client.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
            )
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs", [])
                if pairs:
                    pair = pairs[0]
                    base = pair.get("baseToken", {})
                    return {
                        "name": base.get("name", "Unknown"),
                        "symbol": base.get("symbol", "???"),
                        "mint": mint,
                        "image": pair.get("info", {}).get("imageUrl", ""),
                    }
        return None

    async def _from_pumpfun_api(self, mint: str) -> Optional[Dict]:
        """Fetch from pump.fun frontend API."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://frontend-api-v3.pump.fun/coins/{mint}"
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "name": data.get("name", "Unknown"),
                    "symbol": data.get("symbol", "???"),
                    "mint": mint,
                    "image": data.get("image_uri", ""),
                    "uri": data.get("metadata_uri", ""),
                    "description": data.get("description", ""),
                    "twitter": data.get("twitter", ""),
                    "telegram": data.get("telegram", ""),
                    "website": data.get("website", ""),
                    "creator": data.get("creator", ""),
                    "bonding_curve": data.get("bonding_curve", ""),
                    "complete": data.get("complete", False),  # True = graduated
                    "virtual_sol_reserves": data.get("virtual_sol_reserves"),
                    "virtual_token_reserves": data.get("virtual_token_reserves"),
                    "total_supply": data.get("total_supply"),
                }
        return None

    async def _from_helius_das(self, mint: str) -> Optional[Dict]:
        """Fetch from Helius Digital Asset Standard API."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                self.helius_rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": "token-resolve",
                    "method": "getAsset",
                    "params": {"id": mint},
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("result", {})
                content = result.get("content", {})
                metadata = content.get("metadata", {})
                return {
                    "name": metadata.get("name", "Unknown"),
                    "symbol": metadata.get("symbol", "???"),
                    "mint": mint,
                    "image": content.get("links", {}).get("image", ""),
                    "uri": content.get("json_uri", ""),
                }
        return None

    async def get_full_pumpfun_data(self, mint: str) -> Optional[Dict]:
        """Get complete pump.fun data including bonding curve status."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"https://frontend-api-v3.pump.fun/coins/{mint}"
                )
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.error(f"Failed to get pump.fun data for {mint}: {e}")
        return None

    def clear_cache(self):
        """Clear the metadata cache."""
        self._cache.clear()

    def cache_size(self) -> int:
        return len(self._cache)
