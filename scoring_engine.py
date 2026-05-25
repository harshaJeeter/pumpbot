"""
Scoring Engine - Multi-factor token scoring for buy decisions.
Returns a composite score 0-100. Only buy if score >= MIN_SCORE.
"""

import re
import time
import logging
from typing import Dict, Tuple

import config

logger = logging.getLogger(__name__)


class ScoringEngine:
    """
    Scores tokens on multiple factors:
    - Momentum (30%): MC growth rate, buy pressure
    - Holders (25%): Distribution quality, unique count
    - Social (15%): Twitter, Telegram, Website
    - Liquidity (15%): Depth relative to MC
    - Name (10%): Meme virality potential
    - Volume (5%): Trading activity
    """

    # Name keywords that indicate meme potential
    HIGH_VALUE_NAMES = {
        "pepe", "doge", "dog", "cat", "frog", "moon", "gem",
        "diamond", "rocket", "lambo", "gold", "cash", "bull",
    }
    MEDIUM_VALUE_NAMES = {
        "sol", "bonk", "shib", "inu", "star", "king", "chad",
        "wolf", "bear", "ape", "based", "sigma", "giga", "mega",
        "trump", "elon", "ai", "gpt", "meme", "wojak", "npc",
    }

    def __init__(self):
        self._momentum_history: Dict[str, list] = {}  # mint -> [(timestamp, mc)]

    def score(self, token_data: Dict) -> Tuple[int, Dict[str, int]]:
        """
        Score a token. Returns (composite_score, breakdown_dict).
        """
        breakdown = {}

        # 1. Momentum Score (0-100)
        momentum = self._score_momentum(token_data)
        breakdown["momentum"] = momentum

        # 2. Holder Score (0-100)
        holders = self._score_holders(token_data)
        breakdown["holders"] = holders

        # 3. Social Score (0-100)
        social = self._score_social(token_data)
        breakdown["social"] = social

        # 4. Liquidity Score (0-100)
        liquidity = self._score_liquidity(token_data)
        breakdown["liquidity"] = liquidity

        # 5. Name Score (0-100)
        name_score = self._score_name(token_data)
        breakdown["name"] = name_score

        # 6. Volume Score (0-100)
        volume = self._score_volume(token_data)
        breakdown["volume"] = volume

        # Weighted composite
        composite = int(
            momentum * config.WEIGHT_MOMENTUM +
            holders * config.WEIGHT_HOLDER +
            social * config.WEIGHT_SOCIAL +
            liquidity * config.WEIGHT_LIQUIDITY +
            name_score * config.WEIGHT_NAME +
            volume * config.WEIGHT_VOLUME
        )

        breakdown["composite"] = composite
        return composite, breakdown

    def _score_momentum(self, data: Dict) -> int:
        """Score based on market cap growth velocity."""
        score = 0
        mint = data.get("mint", "")
        mc = data.get("market_cap_usd", 0)

        if not mc or mc <= 0:
            return 0

        # Record MC for this token
        now = time.time()
        if mint not in self._momentum_history:
            self._momentum_history[mint] = []
        self._momentum_history[mint].append((now, mc))

        # Keep only last 5 minutes of data
        cutoff = now - 300
        self._momentum_history[mint] = [
            (t, m) for t, m in self._momentum_history[mint] if t > cutoff
        ]

        history = self._momentum_history[mint]
        if len(history) < 2:
            # First time seeing this token - use price change data if available
            price_change_5m = data.get("price_change_5m", 0)
            if price_change_5m > 50:
                score = 90
            elif price_change_5m > 30:
                score = 75
            elif price_change_5m > 15:
                score = 60
            elif price_change_5m > 5:
                score = 40
            elif price_change_5m > 0:
                score = 25
            else:
                score = 10
        else:
            # Calculate growth rate
            oldest_t, oldest_mc = history[0]
            newest_t, newest_mc = history[-1]
            time_diff = newest_t - oldest_t

            if time_diff > 0 and oldest_mc > 0:
                growth_pct = ((newest_mc - oldest_mc) / oldest_mc) * 100
                growth_per_min = growth_pct / (time_diff / 60)

                if growth_per_min > 20:
                    score = 95
                elif growth_per_min > 10:
                    score = 80
                elif growth_per_min > 5:
                    score = 65
                elif growth_per_min > 2:
                    score = 50
                elif growth_per_min > 0:
                    score = 30
                else:
                    score = 10  # Declining

        # Bonus: Volume surge indicates momentum
        volume_5m = data.get("volume_5m_usd", 0)
        if volume_5m > 5000:
            score = min(100, score + 10)

        return min(100, max(0, score))

    def _score_holders(self, data: Dict) -> int:
        """Score based on holder count and distribution."""
        score = 0
        holder_count = data.get("holder_count", 0)
        holders = data.get("holders", [])
        total_supply = data.get("total_supply", 0)

        # Holder count scoring
        if holder_count >= 200:
            score += 40
        elif holder_count >= 100:
            score += 35
        elif holder_count >= 50:
            score += 25
        elif holder_count >= 30:
            score += 15
        else:
            score += 5

        # Distribution scoring
        if holders and total_supply > 0:
            # Check top holder concentration
            if len(holders) >= 1:
                top_pct = (holders[0].get("balance", 0) / total_supply) * 100
                if top_pct < 5:
                    score += 30  # Very well distributed
                elif top_pct < 10:
                    score += 20
                elif top_pct < 15:
                    score += 10
                elif top_pct < 25:
                    score += 5
                else:
                    score -= 10  # Whale alert

            # Check top 5 concentration
            if len(holders) >= 5:
                top5_pct = sum(h.get("balance", 0) for h in holders[:5]) / total_supply * 100
                if top5_pct < 20:
                    score += 20
                elif top5_pct < 35:
                    score += 10
                elif top5_pct < 50:
                    score += 5
                else:
                    score -= 10

        # Unique recent buyers indicate organic interest
        recent_buys = data.get("recent_unique_buyers", 0)
        if recent_buys > 10:
            score += 10
        elif recent_buys > 5:
            score += 5

        return min(100, max(0, score))

    def _score_social(self, data: Dict) -> int:
        """Score based on social media presence."""
        score = 0

        has_twitter = bool(data.get("twitter"))
        has_telegram = bool(data.get("telegram"))
        has_website = bool(data.get("website"))

        if has_twitter:
            score += 40
        if has_telegram:
            score += 25
        if has_website:
            score += 20

        # Bonus for verified/substantial social presence
        if has_twitter and has_website:
            score += 15

        return min(100, max(0, score))

    def _score_liquidity(self, data: Dict) -> int:
        """Score based on liquidity depth."""
        score = 0
        liquidity = data.get("liquidity_usd", 0)
        mc = data.get("market_cap_usd", 0)

        if liquidity <= 0:
            return 0

        # Absolute liquidity
        if liquidity > 20000:
            score += 40
        elif liquidity > 10000:
            score += 30
        elif liquidity > 5000:
            score += 20
        elif liquidity > 3000:
            score += 10
        else:
            score += 5

        # Liquidity-to-MC ratio (higher = more exit liquidity)
        if mc > 0:
            ratio = liquidity / mc
            if ratio > 0.5:
                score += 40
            elif ratio > 0.3:
                score += 30
            elif ratio > 0.15:
                score += 20
            elif ratio > 0.05:
                score += 10
            else:
                score += 5

        # Extra: growing liquidity = bullish
        liq_change = data.get("liquidity_change_5m", 0)
        if liq_change > 0:
            score += 10

        return min(100, max(0, score))

    def _score_name(self, data: Dict) -> int:
        """Score based on meme potential of name/symbol."""
        score = 30  # Base score for having a valid name

        name = (data.get("name") or "").lower()
        symbol = (data.get("symbol") or "").lower()

        if not name or name in ("unknown", "?"):
            return 0

        # High value keywords
        for keyword in self.HIGH_VALUE_NAMES:
            if keyword in name or keyword in symbol:
                score += 25
                break

        # Medium value keywords
        for keyword in self.MEDIUM_VALUE_NAMES:
            if keyword in name or keyword in symbol:
                score += 15
                break

        # Symbol length (short memorable symbols are better)
        if 3 <= len(symbol) <= 6:
            score += 15
        elif 2 <= len(symbol) <= 8:
            score += 10

        # Penalize bad patterns
        if re.match(r"^0x", symbol):
            score -= 30
        if re.match(r"^[A-Z]{8,}$", symbol):
            score -= 20
        if any(c in symbol for c in "!@#$%^&*()"):
            score -= 25

        # Alliterative/catchy names
        if name and len(set(name[:3])) <= 2:  # Repeated sounds
            score += 5

        return min(100, max(0, score))

    def _score_volume(self, data: Dict) -> int:
        """Score based on trading volume."""
        score = 0
        volume_5m = data.get("volume_5m_usd", 0)
        volume_1h = data.get("volume_1h_usd", 0)
        mc = data.get("market_cap_usd", 0)

        # Absolute volume
        if volume_5m > 10000:
            score += 40
        elif volume_5m > 5000:
            score += 30
        elif volume_5m > 2000:
            score += 20
        elif volume_5m > 500:
            score += 10

        # Volume/MC ratio (turnover)
        if mc > 0 and volume_1h > 0:
            turnover = volume_1h / mc
            if turnover > 1.0:
                score += 30
            elif turnover > 0.5:
                score += 20
            elif turnover > 0.2:
                score += 15
            elif turnover > 0.1:
                score += 10

        # Buy/sell ratio
        buys = data.get("buys_5m", 0)
        sells = data.get("sells_5m", 0)
        if buys > 0 and sells >= 0:
            buy_ratio = buys / max(buys + sells, 1)
            if buy_ratio > 0.7:
                score += 20  # Strong buy pressure
            elif buy_ratio > 0.55:
                score += 10

        return min(100, max(0, score))

    def record_mc(self, mint: str, mc: float):
        """Record a market cap data point for momentum tracking."""
        now = time.time()
        if mint not in self._momentum_history:
            self._momentum_history[mint] = []
        self._momentum_history[mint].append((now, mc))

        # Prune old data (keep 10 min)
        cutoff = now - 600
        self._momentum_history[mint] = [
            (t, m) for t, m in self._momentum_history[mint] if t > cutoff
        ]

    def cleanup_old_data(self, max_age_seconds: int = 900):
        """Remove momentum data for tokens we're no longer tracking."""
        now = time.time()
        to_remove = []
        for mint, history in self._momentum_history.items():
            if not history or (now - history[-1][0]) > max_age_seconds:
                to_remove.append(mint)
        for mint in to_remove:
            del self._momentum_history[mint]
