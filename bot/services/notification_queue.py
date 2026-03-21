# bot/services/notification_queue.py
"""Rate-limited notification queue to prevent Telegram API limits."""

import asyncio
import logging
import itertools  # ✅ Add this import
from dataclasses import dataclass
from typing import Optional, Any
from datetime import datetime, timezone
from telegram.error import RetryAfter

logger = logging.getLogger(__name__)


@dataclass
class QueuedNotification:
    """Represents a notification to be sent"""
    user_id: int
    message: str
    parse_mode: Optional[str] = 'Markdown'
    reply_markup: Optional[Any] = None
    priority: int = 0  # Higher = sent first
    created_at: datetime = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)


MAX_RETRIES = 3


class NotificationQueue:
    """Thread-safe notification queue with rate limiting."""
    
    def __init__(self, bot, rate_limit: int = 25):
        """
        Args:
            bot: Telegram bot instance
            rate_limit: Maximum messages per second
        """
        self.bot = bot
        self.rate_limit = rate_limit
        self.queue = asyncio.PriorityQueue()
        self.processing = False
        self._processor_task = None
        self._counter = itertools.count()  # ✅ Add unique counter for tiebreaking
        self.stats = {
            'sent': 0,
            'failed': 0,
            'queued': 0
        }
    
    async def start(self):
        """Start the notification processor"""
        if self.processing:
            return
        
        self.processing = True
        self._processor_task = asyncio.create_task(self._process_queue())
        logger.info("✅ Notification queue processor started")
    
    async def stop(self):
        """Stop the notification processor"""
        self.processing = False
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        logger.info("✅ Notification queue processor stopped")
    
    async def enqueue(
        self, 
        user_id: int, 
        message: str,
        parse_mode: str = 'Markdown',
        reply_markup: Any = None,
        priority: int = 0
    ):
        """Add a notification to the queue"""
        notification = QueuedNotification(
            user_id=user_id,
            message=message,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            priority=priority
        )
        
        # ✅ Priority queue: (negative_priority, counter, notification)
        # Counter ensures unique ordering even for same priority
        count = next(self._counter)
        await self.queue.put((-priority, count, notification))
        self.stats['queued'] += 1
    
    async def _process_queue(self):
        """Background task that processes queued notifications"""
        logger.info(f"📤 Notification processor running (rate limit: {self.rate_limit}/s)")
        
        while self.processing:
            try:
                # Get notification from queue (with timeout to allow clean shutdown)
                try:
                    # ✅ Unpack with counter
                    _, _, notification = await asyncio.wait_for(
                        self.queue.get(), 
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                # Send the notification with retry logic for rate limits
                try:
                    await self._send_with_retry(
                        self.bot.send_message,
                        chat_id=notification.user_id,
                        text=notification.message,
                        parse_mode=notification.parse_mode,
                        reply_markup=notification.reply_markup
                    )
                    self.stats['sent'] += 1
                    
                except Exception as e:
                    logger.error(f"Failed to send notification to {notification.user_id}: {e}")
                    self.stats['failed'] += 1
                
                # Rate limiting: wait between sends
                await asyncio.sleep(1 / self.rate_limit)
                
            except Exception as e:
                logger.error(f"Error in notification processor: {e}")
                await asyncio.sleep(1)
    
    async def _send_with_retry(self, send_func, *args, **kwargs):
        """Send with exponential backoff for rate limits."""
        for attempt in range(MAX_RETRIES):
            try:
                return await send_func(*args, **kwargs)
            except RetryAfter as e:
                wait_time = e.retry_after + 1  # Add 1s buffer
                logger.warning(f"Rate limited by Telegram, waiting {wait_time}s (attempt {attempt + 1}/{MAX_RETRIES})")
                await asyncio.sleep(wait_time)
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    wait_time = (2 ** attempt) * 1  # 1s, 2s, 4s
                    logger.warning(f"Send failed, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    raise
        raise Exception(f"Failed to send after {MAX_RETRIES} retries")

    def get_stats(self) -> dict:
        """Get queue statistics"""
        return {
            **self.stats,
            'pending': self.queue.qsize()
        }
