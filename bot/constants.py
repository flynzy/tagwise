"""Constants and mappings for the Tagwise bot."""

# Time period mapping: user input -> API value
TIME_PERIOD_MAP = {
    'daily': 'DAY',
    'day': 'DAY',
    'd': 'DAY',
    '1d': 'DAY',
    'weekly': 'WEEK',
    'week': 'WEEK',
    'w': 'WEEK',
    '7d': 'WEEK',
    'monthly': 'MONTH',
    'month': 'MONTH',
    'm': 'MONTH',
    '30d': 'MONTH',
    'all': 'ALL',
    'alltime': 'ALL',
    'all-time': 'ALL',
    'a': 'ALL',
}

TIME_PERIOD_DISPLAY = {
    'DAY': 'Daily',
    'WEEK': 'Weekly',
    'MONTH': 'Monthly',
    'ALL': 'All-Time',
}

# Category mapping: user input -> API value
CATEGORY_MAP = {
    'overall': 'OVERALL',
    'all': 'OVERALL',
    'politics': 'POLITICS',
    'pol': 'POLITICS',
    'sports': 'SPORTS',
    'sport': 'SPORTS',
    'crypto': 'CRYPTO',
    'culture': 'CULTURE',
    'mentions': 'MENTIONS',
    'weather': 'WEATHER',
    'economics': 'ECONOMICS',
    'econ': 'ECONOMICS',
    'tech': 'TECH',
    'technology': 'TECH',
    'finance': 'FINANCE',
    'fin': 'FINANCE',
}

CATEGORY_DISPLAY = {
    'OVERALL': '🌐 Overall',
    'POLITICS': '🏛️ Politics',
    'SPORTS': '⚽ Sports',
    'CRYPTO': '₿ Crypto',
    'CULTURE': '🎭 Culture',
    'MENTIONS': '💬 Mentions',
    'WEATHER': '🌤️ Weather',
    'ECONOMICS': '📈 Economics',
    'TECH': '💻 Tech',
    'FINANCE': '💰 Finance',
}

CATEGORY_EMOJI = {
    'OVERALL': '🌐',
    'POLITICS': '🏛️',
    'SPORTS': '⚽',
    'CRYPTO': '₿',
    'CULTURE': '🎭',
    'MENTIONS': '💬',
    'WEATHER': '🌤️',
    'ECONOMICS': '📈',
    'TECH': '💻',
    'FINANCE': '💰',
}

# Number of top traders to track from leaderboard
LEADERBOARD_TRACK_LIMIT = 5