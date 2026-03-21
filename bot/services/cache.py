# bot/cache.py
"""Redis caching layer."""

import json
import redis.asyncio as redis
from typing import Optional, Any
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


class CacheManager:
    """Redis-based caching with automatic serialization."""
    
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.client: Optional[redis.Redis] = None
    
    async def connect(self):
        self.client = redis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
        await self.client.ping()
        logger.info("Redis connected")
    
    async def close(self):
        if self.client:
            await self.client.close()
    
    async def get(self, key: str) -> Optional[Any]:
        """Get cached value."""
        data = await self.client.get(key)
        if data:
            return json.loads(data)
        return None
    
    async def set(
        self, 
        key: str, 
        value: Any, 
        ttl: timedelta = timedelta(minutes=5)
    ):
        """Set cached value with TTL."""
        await self.client.setex(
            key, 
            int(ttl.total_seconds()), 
            json.dumps(value)
        )
    
    async def delete(self, key: str):
        """Delete cached value."""
        await self.client.delete(key)
    
    # === ADD THESE METHODS for polymarket_client.py compatibility ===
    
    async def get_json(self, key: str) -> Optional[Any]:
        """Get JSON value from cache (alias for get())."""
        return await self.get(key)
    
    async def set_json(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Set JSON value with TTL in seconds (not timedelta)."""
        try:
            await self.set(key, value, timedelta(seconds=ttl))
            return True
        except Exception as e:
            logger.error(f"Cache set_json error: {e}")
            return False
    
    # === END NEW METHODS ===
    
    # State management for cross-instance coordination
    async def add_awaiting_input(self, user_id: int, input_type: str, ttl: int = 300):
        """Track users awaiting input (replaces in-memory set)."""
        await self.client.setex(
            f"awaiting:{user_id}",
            ttl,
            input_type
        )
    
    async def get_awaiting_input(self, user_id: int) -> Optional[str]:
        """Check if user is awaiting input."""
        return await self.client.get(f"awaiting:{user_id}")
    
    async def remove_awaiting_input(self, user_id: int):
        """Remove user from awaiting state."""
        await self.client.delete(f"awaiting:{user_id}")
    
    # Distributed locking for scheduled tasks
    async def acquire_lock(self, lock_name: str, ttl: int = 60) -> bool:
        """Acquire a distributed lock."""
        return await self.client.set(
            f"lock:{lock_name}",
            "1",
            nx=True,  # Only set if not exists
            ex=ttl
        )
    
    async def release_lock(self, lock_name: str):
        """Release a distributed lock."""
        await self.client.delete(f"lock:{lock_name}")
    
    # === User query caching (short-TTL) ===
    
    async def get_cached_is_pro(self, user_id: int) -> Optional[bool]:
        """Get cached is_pro status for a user."""
        data = await self.client.get(f"user:is_pro:{user_id}")
        if data is not None:
            return data == "1"
        return None
    
    async def set_cached_is_pro(self, user_id: int, is_pro: bool, ttl: int = 30):
        """Cache is_pro status with short TTL (default 30s)."""
        await self.client.setex(f"user:is_pro:{user_id}", ttl, "1" if is_pro else "0")
    
    async def invalidate_is_pro(self, user_id: int):
        """Invalidate cached is_pro status (call after subscription changes)."""
        await self.client.delete(f"user:is_pro:{user_id}")
    
    async def get_cached_tracked_wallets(self, user_id: int) -> Optional[Any]:
        """Get cached tracked wallets for a user."""
        return await self.get(f"user:wallets:{user_id}")
    
    async def set_cached_tracked_wallets(self, user_id: int, wallets: Any, ttl: int = 30):
        """Cache tracked wallets with short TTL (default 30s)."""
        await self.set(f"user:wallets:{user_id}", wallets, timedelta(seconds=ttl))
    
    async def invalidate_tracked_wallets(self, user_id: int):
        """Invalidate cached tracked wallets (call after add/remove wallet)."""
        await self.client.delete(f"user:wallets:{user_id}")