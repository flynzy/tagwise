# bot/services/cache_strategies.py
"""Advanced caching strategies for performance optimization."""

import json
import inspect
import logging
from typing import Optional, Any, Callable
from datetime import timedelta
from functools import wraps

logger = logging.getLogger(__name__)


class CacheStrategy:
    """Advanced caching with multiple strategies"""
    
    def __init__(self, cache_manager):
        self.cache = cache_manager
    
    async def get_or_fetch(
        self, 
        key: str, 
        fetch_func: Callable,
        ttl: int = 300,
        force_refresh: bool = False
    ) -> Any:
        """
        Get from cache or fetch and cache.
        
        Args:
            key: Cache key
            fetch_func: Async function to fetch data if not cached
            ttl: Time to live in seconds
            force_refresh: Skip cache and force fetch
        """
        if not force_refresh:
            cached = await self.cache.get(key)
            if cached is not None:
                logger.debug(f"Cache HIT: {key}")
                return cached
        
        logger.debug(f"Cache MISS: {key}")
        data = await fetch_func()
        
        if data is not None:
            await self.cache.set(key, data, timedelta(seconds=ttl))
        
        return data
    
    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Batch get multiple cache keys.
        
        TODO: Replace with Redis MGET pipeline for better performance.
        """
        results = {}
        
        for key in keys:
            value = await self.cache.get(key)
            if value is not None:
                results[key] = value
        
        return results
    
    async def set_many(self, items: dict[str, Any], ttl: int = 300):
        """Batch set multiple cache keys"""
        for key, value in items.items():
            await self.cache.set(key, value, timedelta(seconds=ttl))
    
    async def invalidate_pattern(self, pattern: str):
        """Invalidate all keys matching a pattern."""
        try:
            deleted = 0
            async for key in self.cache.client.scan_iter(match=pattern):
                await self.cache.client.delete(key)
                deleted += 1
            logger.info(f"Invalidated {deleted} keys matching pattern: {pattern}")
        except Exception as e:
            logger.error(f"Error invalidating cache pattern {pattern}: {e}")
    
    def cached(self, ttl: int = 300, key_prefix: str = ""):
        """
        Decorator for caching async function results.
        
        Usage:
            @cache_strategy.cached(ttl=600, key_prefix="wallet_stats")
            async def get_wallet_stats(address: str):
                return await fetch_stats(address)
        """
        def decorator(func: Callable):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Build cache key from function name and arguments
                key_parts = [key_prefix or func.__name__]
                
                # Skip 'self' or 'cls' for methods
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                if params and params[0] in ('self', 'cls'):
                    cache_args = args[1:]  # Skip self/cls
                else:
                    cache_args = args
                
                # Add positional args
                key_parts.extend(str(arg) for arg in cache_args)
                
                # Add keyword args
                for k, v in sorted(kwargs.items()):
                    key_parts.append(f"{k}={v}")
                
                cache_key = ":".join(key_parts)
                
                # Try cache first
                cached = await self.cache.get(cache_key)
                if cached is not None:
                    logger.debug(f"Cache HIT: {cache_key}")
                    return cached
                
                # Fetch and cache
                logger.debug(f"Cache MISS: {cache_key}")
                result = await func(*args, **kwargs)
                
                if result is not None:
                    await self.cache.set(cache_key, result, timedelta(seconds=ttl))
                
                return result
            
            return wrapper
        return decorator


class WalletStatsCache:
    """Specialized cache for wallet statistics"""
    
    def __init__(self, cache_strategy: CacheStrategy):
        self.cache = cache_strategy
    
    async def get_wallet_stats(self, address: str) -> Optional[dict]:
        """Get cached wallet stats"""
        key = f"wallet_stats:{address.lower()}"
        return await self.cache.cache.get(key)
    
    async def set_wallet_stats(self, address: str, stats: dict, ttl: int = 3600):
        """Cache wallet stats"""
        key = f"wallet_stats:{address.lower()}"
        await self.cache.cache.set(key, stats, timedelta(seconds=ttl))
    
    async def invalidate_wallet(self, address: str):
        """Invalidate wallet cache when new trade detected"""
        key = f"wallet_stats:{address.lower()}"
        await self.cache.cache.delete(key)


class MarketDataCache:
    """Specialized cache for market data"""
    
    def __init__(self, cache_strategy: CacheStrategy):
        self.cache = cache_strategy
    
    async def get_market(self, market_id: str) -> Optional[dict]:
        """Get cached market data"""
        key = f"market:{market_id}"
        return await self.cache.cache.get(key)
    
    async def set_market(self, market_id: str, data: dict, ttl: int = 600):
        """Cache market data"""
        key = f"market:{market_id}"
        await self.cache.cache.set(key, data, timedelta(seconds=ttl))
    
    async def get_leaderboard(
        self, 
        time_period: str, 
        category: str, 
        order_by: str
    ) -> Optional[list]:
        """Get cached leaderboard"""
        key = f"leaderboard:{time_period}:{category}:{order_by}"
        return await self.cache.cache.get(key)
    
    async def set_leaderboard(
        self, 
        time_period: str, 
        category: str, 
        order_by: str,
        data: list,
        ttl: int = 21600
    ):
        """Cache leaderboard data"""
        key = f"leaderboard:{time_period}:{category}:{order_by}"
        await self.cache.cache.set(key, data, timedelta(seconds=ttl))


class DistributedLock:
    """Distributed locking using Redis for horizontal scaling"""
    
    def __init__(self, cache_manager, lock_name: str, ttl: int = 60):
        self.cache = cache_manager
        self.lock_name = lock_name
        self.ttl = ttl
        self.acquired = False
    
    async def __aenter__(self):
        """Acquire lock"""
        self.acquired = await self.cache.acquire_lock(self.lock_name, self.ttl)
        if not self.acquired:
            logger.warning(f"Failed to acquire lock: {self.lock_name}")
        return self.acquired
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Release lock"""
        if self.acquired:
            await self.cache.release_lock(self.lock_name)
