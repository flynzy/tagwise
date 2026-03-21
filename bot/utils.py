from datetime import datetime
from typing import Dict, List, Tuple


def format_number(num: float, decimals: int = 2) -> str:
    """Format number with commas and decimals"""
    return f"{num:,.{decimals}f}"


def format_percentage(num: float) -> str:
    """Format number as percentage"""
    return f"{num:+.2f}%"


def format_tier_badge(tier: str) -> str:
    """Format tier as a badge"""
    if tier == "PRO":
        return "🔥 PRO"
    return "🆓 FREE"


