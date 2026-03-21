# polymarket_client.py (Async + Redis Cache with Strategy Pattern)

import logging
import asyncio
import json
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import httpx
from bot.services.cache import CacheManager

logger = logging.getLogger(__name__)

# Valid API values for validation
VALID_TIME_PERIODS = {'DAY', 'WEEK', 'MONTH', 'ALL'}
VALID_CATEGORIES = {'OVERALL', 'POLITICS', 'SPORTS', 'CRYPTO', 'CULTURE', 'MENTIONS', 'WEATHER', 'ECONOMICS', 'TECH', 'FINANCE'}
VALID_ORDER_BY = {'PNL', 'VOL'}


class PolymarketClient:
    """Async client for interacting with Polymarket APIs with Redis caching"""
    
    def __init__(self, cache: CacheManager = None):
        self.data_api_base = "https://data-api.polymarket.com"
        self.gamma_api_base = "https://gamma-api.polymarket.com"
        
        # HTTP client (initialized in connect())
        self.client: Optional[httpx.AsyncClient] = None
        
        # Cache manager (legacy support)
        self.cache = cache
        
        # New cache strategy (initialized via set_cache())
        self.cache_strategy = None
        self.market_cache = None
        
        # Fallback in-memory cache if Redis unavailable
        self._memory_cache: Dict[str, tuple[float, any]] = {}
    
    def set_cache(self, cache_manager):
        """Initialize cache strategies (called from bot core)"""
        try:
            from bot.services.cache_strategies import CacheStrategy, MarketDataCache
            self.cache = cache_manager
            self.cache_strategy = CacheStrategy(cache_manager)
            self.market_cache = MarketDataCache(self.cache_strategy)
            logger.info("✅ Cache strategies initialized")
        except ImportError:
            logger.warning("Cache strategies not available, using legacy caching")
    
    async def connect(self):
        """Initialize async HTTP client"""
        self.client = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=100
            ),
            headers={
                'Accept': 'application/json',
                'User-Agent': 'TagwiseBot/1.0'
            }
        )
        logger.info("✅ Polymarket client connected")
    
    async def close(self):
        """Close async HTTP client"""
        if self.client:
            await self.client.aclose()
            logger.info("Polymarket client closed")
    
    async def _make_request(self, url: str, params: dict = None) -> dict | list | None:
        """Make an async GET request with error handling and rate limiting"""
        if not self.client:
            await self.connect()
        
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"API request failed: {url} - {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in API request: {e}")
            return None
    
    async def _get_cached(self, key: str, ttl: int = 3600) -> Optional[any]:
        """Get value from cache (Redis or memory fallback) - LEGACY METHOD"""
        # Try Redis first
        if self.cache:
            cached = await self.cache.get_json(key)
            if cached:
                return cached
        
        # Fallback to memory cache
        if key in self._memory_cache:
            cached_time, cached_value = self._memory_cache[key]
            if (datetime.now().timestamp() - cached_time) < ttl:
                return cached_value
            else:
                # Expired
                del self._memory_cache[key]
        
        return None
    
    async def _set_cached(self, key: str, value: any, ttl: int = 3600):
        """Set value in cache (Redis or memory fallback) - LEGACY METHOD"""
        # Try Redis first
        if self.cache:
            success = await self.cache.set_json(key, value, ttl)
            if success:
                return
        
        # Fallback to memory cache
        self._memory_cache[key] = (datetime.now().timestamp(), value)
    
    async def get_profile(self, wallet_address: str) -> dict:
        """Get user profile information"""
        try:
            url = f"{self.gamma_api_base}/public-profile"
            params = {'address': wallet_address}
            
            response = await self.client.get(url, params=params)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                logger.info(f"No public profile found for {wallet_address}")
                return {}
            else:
                logger.error(f"Error fetching profile: {response.status_code}")
                return {}
        except Exception as e:
            logger.error(f"Exception fetching profile: {e}")
            return {}

    async def get_wallet_stats(
        self, 
        wallet_address: str,
        use_cache: bool = True,
        cache_ttl: int = 300
    ) -> dict:
        """Get comprehensive wallet statistics with caching"""
        cache_key = f"stats:{wallet_address.lower()}"
        
        if use_cache:
            cached = await self._get_cached(cache_key, cache_ttl)
            if cached:
                return cached
        
        wallet_address = wallet_address.lower()
        
        # Fetch all data concurrently
        profile_task = self.get_profile(wallet_address)
        open_pos_task = self.get_open_positions(wallet_address)
        closed_pos_task = self.get_closed_positions(wallet_address)
        activity_all_task = self.get_activity(wallet_address)
        
        profile, open_positions, closed_positions, all_activity = await asyncio.gather(
            profile_task,
            open_pos_task,
            closed_pos_task,
            activity_all_task,
            return_exceptions=True
        )
        
        # Handle exceptions
        if isinstance(profile, Exception):
            logger.error(f"Error fetching profile: {profile}")
            profile = {}
        if isinstance(open_positions, Exception):
            logger.error(f"Error fetching open positions: {open_positions}")
            open_positions = []
        if isinstance(closed_positions, Exception):
            logger.error(f"Error fetching closed positions: {closed_positions}")
            closed_positions = []
        if isinstance(all_activity, Exception):
            logger.error(f"Error fetching all activity: {all_activity}")
            all_activity = []
        
        # ===== Calculate All-Time PnL =====
        total_pnl = 0.0
        open_pnl = 0.0
        closed_pnl = 0.0
        
        for pos in open_positions:
            try:
                cash_pnl = float(pos.get('cashPnl', 0) or 0)
                open_pnl += cash_pnl
                total_pnl += cash_pnl
            except (ValueError, TypeError):
                continue
        
        for pos in closed_positions:
            try:
                realized_pnl = float(pos.get('realizedPnl', 0) or 0)
                closed_pnl += realized_pnl
                total_pnl += realized_pnl
            except (ValueError, TypeError):
                continue
        
        # ===== Calculate Win Rate =====
        winning_positions = 0
        losing_positions = 0
        
        # Closed positions: count by realizedPnl
        for pos in closed_positions:
            try:
                realized_pnl = float(pos.get('realizedPnl', 0) or 0)
                if realized_pnl > 0:
                    winning_positions += 1
                elif realized_pnl < 0:
                    losing_positions += 1
            except (ValueError, TypeError):
                continue
        
        # Open positions: curPrice=0 means resolved loss, curPrice>=0.99 means resolved win
        for pos in open_positions:
            try:
                cur_price = float(pos.get('curPrice', -1) or -1)
                initial_value = float(pos.get('initialValue', 0) or 0)
                
                if initial_value <= 0:
                    continue
                
                if cur_price == 0:
                    losing_positions += 1
                elif cur_price >= 0.99:
                    winning_positions += 1
            except (ValueError, TypeError):
                continue
        
        total_resolved = winning_positions + losing_positions
        win_rate = (winning_positions / total_resolved) * 100 if total_resolved > 0 else None
        
        # ===== Calculate ROI (All-Time, Position-Based) =====
        total_invested = 0.0
        
        # Open positions: use initialValue (already in USD: size * avgPrice)
        for pos in open_positions:
            try:
                initial_value = float(pos.get('initialValue', 0) or 0)
                total_invested += initial_value
            except (ValueError, TypeError):
                continue
        
        # Closed positions: calculate cost basis (totalBought is in SHARES, not USD)
        for pos in closed_positions:
            try:
                total_bought = float(pos.get('totalBought', 0) or 0)  # shares
                avg_price = float(pos.get('avgPrice', 0) or 0)        # price per share
                cost_basis = total_bought * avg_price                  # USD invested
                total_invested += cost_basis
            except (ValueError, TypeError):
                continue
        
        # Calculate all-time ROI
        roi = (total_pnl / total_invested) * 100 if total_invested > 1.0 else 0.0
        
        # ===== Calculate 7-Day Volume =====
        seven_days_ago = datetime.now().timestamp() - (7 * 24 * 60 * 60)
        volume_7d = 0.0
        
        for trade in all_activity:
            try:
                trade_timestamp = float(trade.get('timestamp', 0))
                if trade_timestamp > 9999999999:
                    trade_timestamp = trade_timestamp / 1000
                
                if trade_timestamp >= seven_days_ago:
                    usdc_size = float(trade.get('usdcSize', 0) or 0)
                    volume_7d += usdc_size
            except (ValueError, TypeError):
                continue
        
        total_positions = len(open_positions) + len(closed_positions)
        total_trades = len(all_activity)
        
        profile_name = profile.get('name') or profile.get('pseudonym')
        
        stats = {
            'name': profile_name,
            'pseudonym': profile.get('pseudonym'),
            'bio': profile.get('bio'),
            'profile_image': profile.get('profileImage'),
            'pnl_all_time': total_pnl,
            'roi_all_time': roi,
            'total_invested': total_invested,
            'open_pnl': open_pnl,
            'open_positions_count': len(open_positions),
            'realized_pnl': closed_pnl,
            'closed_positions_count': len(closed_positions),
            'winning_positions': winning_positions,
            'losing_positions': losing_positions,
            'win_rate': win_rate,
            'volume_7d': volume_7d,
            'total_trades': total_trades,
            'total_positions': total_positions,
            'trades_capped': total_trades >= 10000,
        }
        
        await self._set_cached(cache_key, stats, cache_ttl)
        
        return stats

    async def get_leaderboard(
        self, 
        limit: int = 20, 
        time_period: str = "ALL",
        category: str = "OVERALL",
        order_by: str = "PNL",
        use_cache: bool = True,
        cache_ttl: int = 6 * 60 * 60  # 6 hours
    ) -> List[Dict]:
        """
        Get top traders from the Polymarket leaderboard with Redis caching.
        
        Args:
            limit: Number of traders to fetch (max 50)
            time_period: Time period - "DAY", "WEEK", "MONTH", "ALL"
            category: Market category - "OVERALL", "POLITICS", "SPORTS", "CRYPTO", etc.
            order_by: Order by "PNL" (profit) or "VOL" (volume)
            use_cache: Whether to use cached results
            cache_ttl: Cache time-to-live in seconds
            
        Returns:
            List of trader dictionaries with address, username, pnl, volume, etc.
        """
        # Normalize and validate parameters
        time_period = time_period.upper()
        category = category.upper()
        order_by = order_by.upper()
        
        if time_period not in VALID_TIME_PERIODS:
            logger.warning(f"Invalid time_period '{time_period}', defaulting to ALL")
            time_period = "ALL"
        
        if category not in VALID_CATEGORIES:
            logger.warning(f"Invalid category '{category}', defaulting to OVERALL")
            category = "OVERALL"
        
        if order_by not in VALID_ORDER_BY:
            logger.warning(f"Invalid order_by '{order_by}', defaulting to PNL")
            order_by = "PNL"
        
        # Use new cache strategy if available
        if use_cache and self.market_cache:
            cached = await self.market_cache.get_leaderboard(
                time_period, category, order_by
            )
            if cached:
                return cached[:limit]
        
        # Fetch from API
        traders = await self._fetch_leaderboard_from_api(
            limit, time_period, category, order_by
        )
        
        # Cache using new strategy if available
        if traders and self.market_cache:
            await self.market_cache.set_leaderboard(
                time_period, category, order_by, traders, ttl=cache_ttl
            )
        
        return traders
    
    async def _fetch_leaderboard_from_api(
        self,
        limit: int,
        time_period: str,
        category: str,
        order_by: str
    ) -> List[Dict]:
        """Internal method to fetch leaderboard from API"""
        url = f"{self.data_api_base}/v1/leaderboard"
        
        params = {
            'limit': min(limit, 50),  # API max is 50
            'timePeriod': time_period,
            'category': category,
            'orderBy': order_by,
        }
        
        logger.info(f"Fetching leaderboard from API: {params}")
        
        try:
            data = await self._make_request(url, params)
            
            if not data:
                logger.warning("Failed to fetch leaderboard data")
                return []
            
            traders = []
            for entry in data:
                # Convert rank from string to int
                rank_str = entry.get('rank', '0')
                try:
                    rank_int = int(rank_str) if rank_str else None
                except (ValueError, TypeError):
                    rank_int = None
                
                # Clean up auto-generated usernames
                username = entry.get('userName')
                if username and len(username) > 40 and '-' in username:
                    username = None  # Auto-generated, use address instead
                
                address = entry.get('proxyWallet', '')
                
                trader = {
                    'address': address.lower(),
                    'username': username,
                    'display_name': username or f"{address[:6]}...{address[-4:]}" if address else "Unknown",
                    'rank': rank_int,
                    'pnl': float(entry.get('pnl', 0) or 0),
                    'volume': float(entry.get('vol', 0) or 0),
                    'profile_image': entry.get('profileImage') or None,
                    'x_username': entry.get('xUsername') or None,
                    'verified': entry.get('verifiedBadge', False)
                }
                traders.append(trader)
            
            logger.info(f"Fetched {len(traders)} traders (period: {time_period}, category: {category})")
            return traders[:limit]
            
        except Exception as e:
            logger.error(f"Error fetching leaderboard: {e}")
            return []
    
    async def get_top_traders(self, limit: int = 20) -> List[Dict]:
        """
        Convenience method to get top traders by PnL.
        Returns list of dicts with 'address' and 'username' keys.
        """
        from bot.config import Config
        
        return await self.get_leaderboard(
            limit=limit,
            time_period=getattr(Config, 'LEADERBOARD_TIME_PERIOD', 'ALL'),
            category=getattr(Config, 'LEADERBOARD_CATEGORY', 'OVERALL'),
            order_by=getattr(Config, 'LEADERBOARD_ORDER_BY', 'PNL'),
            cache_ttl=getattr(Config, 'LEADERBOARD_CACHE_TTL', 6 * 60 * 60)
        )
    
    async def get_open_positions(
        self, 
        wallet_address: str, 
        limit: int = 500,
        use_cache: bool = True,
        cache_ttl: int = 60  # 1 minute cache for positions
    ) -> list:
        """Get all current/open positions for a wallet with pagination and caching"""
        cache_key = f"positions:open:{wallet_address.lower()}"
        
        if use_cache:
            cached = await self._get_cached(cache_key, cache_ttl)
            if cached:
                return cached
        
        all_positions = []
        offset = 0
        
        while True:
            url = f"{self.data_api_base}/positions"
            params = {
                'user': wallet_address,
                'limit': limit,
                'offset': offset,
                'sizeThreshold': 0
            }
            
            positions = await self._make_request(url, params)
            
            if not positions or len(positions) == 0:
                break
            
            all_positions.extend(positions)
            
            if len(positions) < limit:
                break
            
            offset += limit
            
            if offset >= 10000:
                logger.warning(f"Reached max offset for positions: {wallet_address}")
                break
            
            await asyncio.sleep(0.05)  # Rate limiting
        
        logger.debug(f"Fetched {len(all_positions)} open positions for {wallet_address[:8]}...")
        
        # Cache the result
        await self._set_cached(cache_key, all_positions, cache_ttl)
        
        return all_positions
    
    async def get_closed_positions(
        self, 
        wallet_address: str, 
        limit: int = 500,
        use_cache: bool = True,
        cache_ttl: int = 300  # 5 minutes cache
    ) -> list:
        """Get all closed/resolved positions for a wallet with pagination and caching"""
        cache_key = f"positions:closed:{wallet_address.lower()}"
        
        if use_cache:
            cached = await self._get_cached(cache_key, cache_ttl)
            if cached:
                return cached
        
        all_positions = []
        offset = 0
        
        while True:
            url = f"{self.data_api_base}/closed-positions"
            params = {
                'user': wallet_address,
                'limit': limit,
                'offset': offset
            }
            
            positions = await self._make_request(url, params)
            
            if not positions or len(positions) == 0:
                break
            
            all_positions.extend(positions)
            
            if len(positions) < limit:
                break
            
            offset += limit
            
            if offset >= 10000:
                logger.warning(f"Reached max offset for closed positions: {wallet_address}")
                break
            
            await asyncio.sleep(0.05)
        
        logger.debug(f"Fetched {len(all_positions)} closed positions for {wallet_address[:8]}...")
        
        # Cache the result
        await self._set_cached(cache_key, all_positions, cache_ttl)
        
        return all_positions
        
    async def get_activity(
        self,
        wallet_address: str,
        start_time: int = None,
        end_time: int = None,
        limit: int = 1000,
        paginate: bool = True
    ) -> list:
        """Get user activity/trades with optional time filtering and pagination
        
        Args:
            wallet_address: Wallet address
            start_time: Start timestamp in seconds (optional)
            end_time: End timestamp in seconds (optional)
            limit: Records per request (API max is 1000)
            paginate: Whether to fetch all pages or just the first
        """
        all_activity = []
        offset = 0
        
        while True:
            try:
                url = f"{self.data_api_base}/activity"
                params = {
                    'user': wallet_address,
                    'limit': min(limit, 1000),  # API max is 1000
                    'offset': offset
                }
                
                if start_time:
                    params['start'] = start_time
                if end_time:
                    params['end'] = end_time
                
                response = await self.client.get(url, params=params)
                
                if response.status_code != 200:
                    logger.error(f"Error fetching activity: {response.status_code}")
                    break
                
                activity = response.json()
                
                if not activity or len(activity) == 0:
                    break
                
                all_activity.extend(activity)
                
                # Stop if we don't need pagination or got fewer than requested
                if not paginate or len(activity) < min(limit, 1000):
                    break
                
                offset += len(activity)
                
                # Safety cap
                if offset >= 50000:
                    logger.warning(f"Reached max offset for activity: {wallet_address}")
                    break
                
                await asyncio.sleep(0.05)  # Rate limiting
                
            except Exception as e:
                logger.error(f"Exception fetching activity: {e}")
                break
        
        logger.debug(f"Fetched {len(all_activity)} total activities for {wallet_address[:8]}...")
        return all_activity
    
    async def get_recent_trades(
        self, 
        wallet_address: str, 
        since: datetime = None,
        use_cache: bool = False
    ) -> list:
        """Get recent trades for a wallet since a given time"""
        wallet_address = wallet_address.lower()
        
        url = f"{self.data_api_base}/activity"
        params = {
            'user': wallet_address,
            'type': 'TRADE',
            'limit': 100,
            'sortBy': 'TIMESTAMP',
            'sortDirection': 'DESC'
        }
        
        if since:
            start_ts = int(since.timestamp())
            params['start'] = start_ts
        
        logger.debug(f"Fetching trades for {wallet_address[:10]}... since timestamp {params.get('start')}")
        
        trades = await self._make_request(url, params)
        
        if not trades:
            logger.debug(f"No trades returned from API for {wallet_address[:10]}...")
            return []
        
        since_ts = int(since.timestamp()) if since else 0
        
        formatted_trades = []
        for trade in trades:
            trade_ts = trade.get('timestamp', 0)
            
            # === NORMALIZE TIMESTAMP ===
            # If timestamp is in milliseconds (13+ digits), convert to seconds
            if trade_ts > 9999999999:
                trade_ts_normalized = trade_ts // 1000
            else:
                trade_ts_normalized = trade_ts
            
            if trade_ts_normalized <= since_ts:
                continue
            
            formatted_trades.append({
                'timestamp': trade_ts,  # Keep original
                'timestamp_normalized': trade_ts_normalized,  # Add normalized version
                'side': trade.get('side'),
                'size': trade.get('size'),
                'price': trade.get('price'),
                'usdc_size': trade.get('usdcSize'),
                'title': trade.get('title'),
                'outcome': trade.get('outcome'),
                'outcome_index': trade.get('outcomeIndex'),
                'slug': trade.get('slug'),
                'event_slug': trade.get('eventSlug'),
                'transaction_hash': trade.get('transactionHash'),
                'asset': trade.get('asset'),
                'token_id': trade.get('asset'),
                'condition_id': trade.get('conditionId'),
            })
        
        logger.debug(f"Found {len(formatted_trades)} new trades (filtered from {len(trades)} total)")
        
        return formatted_trades
    
    async def get_market_info(self, condition_id: str, use_cache: bool = True, cache_ttl: int = 3600) -> Optional[Dict]:
        """Get market information by condition ID with caching using cache strategy"""
        
        # Use new cache strategy if available
        if self.cache_strategy and use_cache:
            async def fetch_market():
                return await self._fetch_market_from_api(condition_id)
            
            return await self.cache_strategy.get_or_fetch(
                key=f"market:{condition_id}",
                fetch_func=fetch_market,
                ttl=cache_ttl
            )
        
        # Fallback to direct fetch
        return await self._fetch_market_from_api(condition_id)
    
    async def _fetch_market_from_api(self, condition_id: str) -> Optional[Dict]:
        """Internal method to fetch market data from API"""
        # Note: This endpoint may need adjustment based on actual Polymarket API
        url = f"{self.data_api_base}/markets/{condition_id}"
        return await self._make_request(url)
    
    async def invalidate_cache(self, wallet_address: str = None):
        """Invalidate cache for a specific wallet or all caches"""
        if wallet_address:
            wallet_address = wallet_address.lower()
            patterns = [
                f"profile:{wallet_address}",
                f"positions:open:{wallet_address}",
                f"positions:closed:{wallet_address}",
                f"activity:{wallet_address}:all",
                f"stats:{wallet_address}"
            ]
            
            for pattern in patterns:
                if self.cache:
                    await self.cache.delete(pattern)
                if pattern in self._memory_cache:
                    del self._memory_cache[pattern]
            
            logger.info(f"Invalidated cache for wallet {wallet_address[:10]}...")
        else:
            # Clear all memory cache
            self._memory_cache.clear()
            logger.info("Cleared all in-memory cache")


# For backward compatibility and standalone usage
async def init_polymarket_client(cache: CacheManager = None) -> PolymarketClient:
    """Initialize and connect Polymarket client"""
    client = PolymarketClient(cache)
    await client.connect()
    return client