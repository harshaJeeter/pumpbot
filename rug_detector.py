"""
Rug Detector - Identifies potential rug pull tokens.
Multi-factor analysis to avoid scams.
"""

import re
import logging
from typing import Dict, List, Tuple

import config

logger = logging.getLogger(__name__)


class RugDetector:
    """
    Analyzes a token for rug pull indicators.
    Returns a risk score (0-100) and list of red flags.
    Score > 50 = DO NOT BUY.
    """

    # Known scam patterns in names
    SCAM_NAME_PATTERNS = [
        r"^0x[a-f0-9]+",           # Hex-style names
        r"^[A-Z]{10,}$",           # Very long all-caps (generated)
        r"test|scam|rug|honeypot", # Obvious red flags
        r"elon.*musk|musk.*elon",  # Celebrity impersonation
        r"official",                # Fake "official" tokens
        r"airdrop|free.*money",    # Bait names
    ]

    # Known copycat base names (legit projects they copy)
    COPYCAT_BASES = [
        "bonk", "dogwifhat", "wif", "popcat", "mew", "wen",
        "jup", "ray", "marinade", "jito",
    ]

    def __init__(self):
        self._blocklist: set = set()  # Known bad mints
        self._creator_blocklist: set = set()  # Known bad creators

    def analyze(self, token_data: Dict) -> Tuple[int, List[str]]:
        """
        Analyze token for rug indicators.
        Returns (risk_score 0-100, list_of_red_flags).
        """
        flags: List[str] = []
        risk = 0

        # 1. Name analysis
        name_risk, name_flags = self._check_name(token_data)
        risk += name_risk
        flags.extend(name_flags)

        # 2. Creator/holder analysis
        holder_risk, holder_flags = self._check_holders(token_data)
        risk += holder_risk
        flags.extend(holder_flags)

        # 3. Authority checks
        auth_risk, auth_flags = self._check_authorities(token_data)
        risk += auth_risk
        flags.extend(auth_flags)

        # 4. Social presence
        social_risk, social_flags = self._check_socials(token_data)
        risk += social_risk
        flags.extend(social_flags)

        # 5. Bonding curve / supply analysis
        supply_risk, supply_flags = self._check_supply(token_data)
        risk += supply_risk
        flags.extend(supply_flags)

        # 6. Known blocklist
        if token_data.get("mint") in self._blocklist:
            risk += 100
            flags.append("BLOCKLISTED_TOKEN")
        if token_data.get("creator") in self._creator_blocklist:
            risk += 80
            flags.append("BLOCKLISTED_CREATOR")

        return min(risk, 100), flags

    def _check_name(self, data: Dict) -> Tuple[int, List[str]]:
        """Check token name/symbol for scam indicators."""
        risk = 0
        flags = []

        name = (data.get("name") or "").lower()
        symbol = (data.get("symbol") or "").lower()

        # Unknown/missing name
        if not name or name in ("unknown", "?"):
            risk += 20
            flags.append("NAME_UNRESOLVED")

        # Scam patterns
        for pattern in self.SCAM_NAME_PATTERNS:
            if re.search(pattern, name, re.IGNORECASE) or re.search(pattern, symbol, re.IGNORECASE):
                risk += 25
                flags.append(f"SCAM_PATTERN:{pattern}")
                break

        # Copycat detection (e.g., "BONK2", "realBONK")
        for base in self.COPYCAT_BASES:
            if base in name and name != base:
                risk += 15
                flags.append(f"POSSIBLE_COPYCAT:{base}")
                break

        # Very short or very long symbols
        if symbol and (len(symbol) > 10 or len(symbol) < 2):
            risk += 10
            flags.append("UNUSUAL_SYMBOL_LENGTH")

        return risk, flags

    def _check_holders(self, data: Dict) -> Tuple[int, List[str]]:
        """Check holder distribution for concentration."""
        risk = 0
        flags = []

        holders = data.get("holders", [])
        creator = data.get("creator", "")
        total_supply = data.get("total_supply", 0)

        if not holders:
            # Can't verify distribution
            risk += 10
            flags.append("NO_HOLDER_DATA")
            return risk, flags

        # Check creator holding
        if creator and total_supply:
            creator_balance = 0
            for h in holders:
                if h.get("address") == creator:
                    creator_balance = h.get("balance", 0)
                    break

            if total_supply > 0:
                creator_pct = (creator_balance / total_supply) * 100
                if creator_pct > config.RUG_INDICATORS["creator_holds_over_pct"]:
                    risk += 30
                    flags.append(f"CREATOR_HOLDS_{creator_pct:.1f}%")

        # Top 10 concentration
        if len(holders) >= 10 and total_supply > 0:
            top_10_balance = sum(h.get("balance", 0) for h in holders[:10])
            top_10_pct = (top_10_balance / total_supply) * 100
            if top_10_pct > config.RUG_INDICATORS["top_10_hold_over_pct"]:
                risk += 25
                flags.append(f"TOP10_HOLD_{top_10_pct:.1f}%")

        # Very few holders
        holder_count = data.get("holder_count", len(holders))
        if holder_count < config.MIN_HOLDERS:
            risk += 15
            flags.append(f"LOW_HOLDERS:{holder_count}")

        return risk, flags

    def _check_authorities(self, data: Dict) -> Tuple[int, List[str]]:
        """Check mint/freeze authorities."""
        risk = 0
        flags = []

        if data.get("mint_authority_enabled"):
            risk += 30
            flags.append("MINT_AUTHORITY_ACTIVE")

        if data.get("freeze_authority_enabled"):
            risk += 25
            flags.append("FREEZE_AUTHORITY_ACTIVE")

        return risk, flags

    def _check_socials(self, data: Dict) -> Tuple[int, List[str]]:
        """Check social media presence."""
        risk = 0
        flags = []

        has_twitter = bool(data.get("twitter"))
        has_telegram = bool(data.get("telegram"))
        has_website = bool(data.get("website"))

        if not has_twitter and not has_telegram and not has_website:
            risk += 20
            flags.append("NO_SOCIALS")
        elif not has_twitter:
            risk += 10
            flags.append("NO_TWITTER")

        return risk, flags

    def _check_supply(self, data: Dict) -> Tuple[int, List[str]]:
        """Check bonding curve and supply metrics."""
        risk = 0
        flags = []

        # If token is already graduated (completed bonding curve)
        if data.get("complete"):
            # Already on Raydium - can't buy on pump.fun anymore
            risk += 50
            flags.append("ALREADY_GRADUATED")

        # Check virtual reserves ratio
        sol_reserves = data.get("virtual_sol_reserves")
        token_reserves = data.get("virtual_token_reserves")
        if sol_reserves and token_reserves:
            # Very low SOL reserves = almost empty curve
            sol_in_curve = sol_reserves / 1e9  # lamports to SOL
            if sol_in_curve < 1.0:
                risk += 15
                flags.append(f"LOW_CURVE_SOL:{sol_in_curve:.2f}")

        return risk, flags

    def add_to_blocklist(self, mint: str):
        """Add a token to the blocklist."""
        self._blocklist.add(mint)

    def add_creator_to_blocklist(self, creator: str):
        """Add a creator wallet to the blocklist."""
        self._creator_blocklist.add(creator)

    def is_blocked(self, mint: str) -> bool:
        return mint in self._blocklist

    def is_creator_blocked(self, creator: str) -> bool:
        return creator in self._creator_blocklist
