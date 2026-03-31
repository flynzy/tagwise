"""Analytics and confidence scoring for Polymarket trades."""

import logging
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ConfidenceScore:
    """Result of confidence score calculation"""
    score: int  # 1-5 stars
    percentage: float  # 0-100
    factors: Dict[str, any]  # Breakdown of what influenced the score
    display: str  # e.g., "⭐⭐⭐⭐ (82%)"


class ConfidenceScorer:
    """Calculate confidence scores for trades based on wallet performance and trade characteristics."""
    
    # Score thresholds for star ratings
    STAR_THRESHOLDS = {
        1: 0,    # 0-39% = ⭐
        2: 40,   # 40-54% = ⭐⭐
        3: 55,   # 55-69% = ⭐⭐⭐
        4: 70,   # 70-84% = ⭐⭐⭐⭐
        5: 85    # 85-100% = ⭐⭐⭐⭐⭐
    }
    
    # Weights for different factors (must sum to 1.0)
    WEIGHTS = {
        'wallet_performance': 0.40,  # Historical win rate & PnL
        'wallet_reputation': 0.20,   # Rank, verified status, experience
        'trade_quality': 0.40        # Price, size, market conditions
    }
    
    def calculate(
        self,
        wallet_stats: Dict,
        trade: Dict,
        is_multi_buy: bool = False
    ) -> ConfidenceScore:
        """
        Calculate confidence score for a trade.
        
        Args:
            wallet_stats: Wallet performance metrics from DB
            trade: Trade details (price, size, side, etc.)
            is_multi_buy: Whether this is part of a multi-buy signal
            
        Returns:
            ConfidenceScore with score, percentage, and factor breakdown
        """
        if not wallet_stats:
            return self._default_score()
        
        # Calculate individual factor scores (0-100 scale)
        perf_score = self._calculate_performance_score(wallet_stats)
        rep_score = self._calculate_reputation_score(wallet_stats)
        trade_score = self._calculate_trade_quality_score(wallet_stats, trade)
        
        # Multi-buy bonus (adds +10 points, capped at 100)
        multi_buy_bonus = 10 if is_multi_buy else 0
        
        # Weighted average
        total_score = (
            perf_score * self.WEIGHTS['wallet_performance'] +
            rep_score * self.WEIGHTS['wallet_reputation'] +
            trade_score * self.WEIGHTS['trade_quality']
        )
        
        # Apply multi-buy bonus
        total_score = min(100, total_score + multi_buy_bonus)
        
        # Convert to star rating
        stars = self._percentage_to_stars(total_score)
        
        # Build factor breakdown
        factors = {
            'wallet_performance': {
                'score': round(perf_score, 1),
                'weight': self.WEIGHTS['wallet_performance'],
                'win_rate': wallet_stats.get('win_rate', 0),
                'total_pnl': wallet_stats.get('total_pnl', 0)
            },
            'wallet_reputation': {
                'score': round(rep_score, 1),
                'weight': self.WEIGHTS['wallet_reputation'],
                'leaderboard_rank': wallet_stats.get('leaderboard_rank'),
                'verified': wallet_stats.get('verified_badge', False),
                'total_trades': wallet_stats.get('total_trades', 0)
            },
            'trade_quality': {
                'score': round(trade_score, 1),
                'weight': self.WEIGHTS['trade_quality'],
                'price': trade.get('price', 0),
                'size': trade.get('usdc_size', 0)
            },
            'multi_buy_bonus': multi_buy_bonus
        }
        
        display = self._format_display(stars, total_score)
        
        return ConfidenceScore(
            score=stars,
            percentage=round(total_score, 1),
            factors=factors,
            display=display
        )
    
    def _calculate_performance_score(self, wallet_stats: Dict) -> float:
        """
        Score based on wallet's historical performance.
        Max: 100 points
        """
        score = 0.0
        
        # Win rate (max 60 points)
        win_rate = wallet_stats.get('win_rate', 0) or 0
        if win_rate > 0:
            # Normalize: if value > 1, assume it's already a percentage (0-100)
            if win_rate > 1:
                win_rate_decimal = win_rate / 100
            else:
                win_rate_decimal = win_rate
            # 50% win rate = 30 pts, 60% = 36 pts, 70% = 42 pts, 100% = 60 pts
            score += min(60, win_rate_decimal * 60)
        
        # Total PnL (max 40 points)
        total_pnl = wallet_stats.get('total_pnl', 0) or 0
        if total_pnl > 0:
            # $1k = 5 pts, $5k = 15 pts, $10k = 25 pts, $50k+ = 40 pts
            pnl_score = min(40, (total_pnl / 1000) * 4)
            score += pnl_score
        elif total_pnl < -1000:
            # Penalty for negative PnL
            score = max(0, score - 20)
        
        return min(100, score)
    
    def _calculate_reputation_score(self, wallet_stats: Dict) -> float:
        """
        Score based on wallet's reputation/credibility.
        Max: 100 points
        """
        score = 0.0
        
        # Leaderboard rank (max 50 points)
        rank = wallet_stats.get('leaderboard_rank')
        if rank:
            if rank <= 10:
                score += 50
            elif rank <= 25:
                score += 40
            elif rank <= 50:
                score += 30
            elif rank <= 100:
                score += 20
            else:
                score += 10
        
        # Verified badge (20 points)
        if wallet_stats.get('verified_badge'):
            score += 20
        
        # Experience/total trades (max 30 points)
        total_trades = wallet_stats.get('total_trades', 0) or 0
        if total_trades >= 100:
            score += 30
        elif total_trades >= 50:
            score += 20
        elif total_trades >= 20:
            score += 10
        elif total_trades >= 5:
            score += 5
        
        return min(100, score)
    
    def _calculate_trade_quality_score(self, wallet_stats: Dict, trade: Dict) -> float:
        """
        Score based on the specific trade characteristics.
        Max: 100 points
        """
        score = 50.0  # Start at neutral
        
        price = trade.get('price', 0.5) or 0.5
        usdc_size = trade.get('usdc_size', 0) or 0
        avg_trade_size = wallet_stats.get('avg_trade_size', 0) or 0
        
        # Price quality (max +/- 30 points)
        # Best prices: 0.30-0.70 (good value, not extreme)
        # Worst: < 0.05 or > 0.95 (risky extremes)
        if 0.30 <= price <= 0.70:
            score += 30  # Sweet spot
        elif 0.20 <= price < 0.30 or 0.70 < price <= 0.80:
            score += 15  # Decent
        elif 0.10 <= price < 0.20 or 0.80 < price <= 0.90:
            score += 0   # Neutral
        elif price < 0.10 or price > 0.90:
            score -= 20  # Very risky
        
        # Trade size relative to wallet's average (max +/- 20 points)
        if avg_trade_size > 0 and usdc_size > 0:
            size_ratio = usdc_size / avg_trade_size
            if 0.8 <= size_ratio <= 2.0:
                # Similar to usual size = confidence
                score += 20
            elif 2.0 < size_ratio <= 3.0:
                # Bigger than usual = strong signal
                score += 15
            elif size_ratio > 5.0:
                # Way bigger = very strong signal
                score += 10
            elif size_ratio < 0.3:
                # Much smaller = weak conviction
                score -= 10
        
        # Minimum trade size filter
        if usdc_size < 50:
            score -= 10  # Small trades less meaningful
        elif usdc_size >= 1000:
            score += 15  # Large position
        elif usdc_size >= 500:
            score += 10  # Significant capital deployed
        
        return max(0, min(100, score))
    
    def _percentage_to_stars(self, percentage: float) -> int:
        """Convert percentage score to 1-5 star rating."""
        if percentage >= self.STAR_THRESHOLDS[5]:
            return 5
        elif percentage >= self.STAR_THRESHOLDS[4]:
            return 4
        elif percentage >= self.STAR_THRESHOLDS[3]:
            return 3
        elif percentage >= self.STAR_THRESHOLDS[2]:
            return 2
        else:
            return 1
    
    def _format_display(self, stars: int, percentage: float) -> str:
        """Format confidence score for display."""
        star_emoji = "⭐" * stars
        return f"{star_emoji} ({percentage:.0f}%)"
    
    def _default_score(self) -> ConfidenceScore:
        """Return default/unknown score when no wallet stats available."""
        return ConfidenceScore(
            score=3,
            percentage=50.0,
            factors={'error': 'No wallet stats available'},
            display="⭐⭐⭐ (50%)"
        )


# ==================== HELPER FUNCTIONS ====================

def get_confidence_emoji(score: int) -> str:
    """Get emoji representation of confidence score."""
    if score >= 5:
        return ""  # Extremely confident
    elif score >= 4:
        return ""  # Very confident
    elif score >= 3:
        return ""  # Neutral
    elif score >= 2:
        return ""  # Low confidence
    else:
        return ""  # Very low confidence


def format_confidence_for_alert(confidence: ConfidenceScore, is_pro: bool = False) -> str:
    """
    Format confidence score for inclusion in trade alerts.
    
    Args:
        confidence: The ConfidenceScore object
        is_pro: Whether user is PRO (determines detail level)
    
    Returns:
        Formatted string for alert message
    """
    if not is_pro:
        return ""  # Free users don't see confidence scores
    
    emoji = get_confidence_emoji(confidence.score)
    
    # Basic display for all PRO users
    result = f"\n\n{emoji} **Confidence:** {confidence.display}"
    
    # Detailed breakdown (optional - can be toggled)
    if confidence.score >= 4:
        result += "\n_High conviction trade_"
    elif confidence.score <= 2:
        result += "\n_Proceed with caution_"
    
    return result


def get_confidence_breakdown_text(confidence: ConfidenceScore) -> str:
    """
    Get detailed breakdown of confidence factors (for /analyze command).
    
    Returns:
        Multi-line formatted breakdown
    """
    factors = confidence.factors
    
    text = f"**Confidence Score:** {confidence.display}\n\n"
    text += "**Factor Breakdown:**\n"
    
    # Performance
    perf = factors.get('wallet_performance', {})
    text += f"• Performance: {perf.get('score', 0):.0f}/100\n"
    text += f"  - Win Rate: {perf.get('win_rate', 0)*100:.0f}%\n"
    text += f"  - Total P&L: ${perf.get('total_pnl', 0):,.0f}\n\n"
    
    # Reputation
    rep = factors.get('wallet_reputation', {})
    text += f"• Reputation: {rep.get('score', 0):.0f}/100\n"
    rank = rep.get('leaderboard_rank')
    text += f"  - Rank: #{rank}" if rank else "  - Rank: Unranked"
    text += f"\n  - Verified: {'✅' if rep.get('verified') else '❌'}\n"
    text += f"  - Experience: {rep.get('total_trades', 0)} trades\n\n"
    
    # Trade Quality
    tq = factors.get('trade_quality', {})
    text += f"• Trade Quality: {tq.get('score', 0):.0f}/100\n"
    text += f"  - Price: {tq.get('price', 0):.2f}\n"
    text += f"  - Size: ${tq.get('size', 0):,.0f}\n"
    
    # Multi-buy bonus
    if factors.get('multi_buy_bonus', 0) > 0:
        text += f"\n🔥 Multi-Buy Bonus: +{factors['multi_buy_bonus']} pts"
    
    return text
