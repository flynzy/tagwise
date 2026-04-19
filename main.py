"""
Tagwise Bot - Polymarket Wallet Tracker
"""

import asyncio
import logging
import signal
import sys
import re

from bot.core import TagwiseBot
from bot.config import Config


class SensitiveDataFilter(logging.Filter):
    """Filter to hide sensitive data like tokens and API keys."""
    
    def filter(self, record):
        # Hide bot token in URLs
        if hasattr(record, 'msg'):
            record.msg = re.sub(
                r'bot\d+:[A-Za-z0-9_-]+',
                'bot***HIDDEN***',
                str(record.msg)
            )
        return True


class CleanFormatter(logging.Formatter):
    """Formatter with colors and clean layout."""
    
    # ANSI color codes
    GREY = '\033[90m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    LEVEL_COLORS = {
        'DEBUG': GREY,
        'INFO': GREEN,
        'WARNING': YELLOW,
        'ERROR': RED,
        'CRITICAL': MAGENTA + BOLD,
    }
    
    def format(self, record):
        # Get color for level
        level_color = self.LEVEL_COLORS.get(record.levelname, self.RESET)
        
        # Color the level name
        colored_level = f"{level_color}{record.levelname:8}{self.RESET}"
        
        # Color the logger name (make it dimmer)
        colored_name = f"{self.GREY}{record.name:30}{self.RESET}"
        
        # Format timestamp (cyan and dimmer)
        timestamp = self.formatTime(record, self.datefmt)
        colored_time = f"{self.CYAN}{timestamp}{self.RESET}"
        
        # Build the message
        message = record.getMessage()
        
        # Special handling for emojis in messages
        if any(emoji in message for emoji in ['🚀', '✅', '❌', '⚠️', '📊', '💰', '🔔', '⏰', '🛑']):
            # Message already has emoji, don't add color
            formatted_msg = f"{colored_level} {colored_time} {colored_name} {message}"
        else:
            # Add subtle color to message based on level
            if record.levelname == 'ERROR' or record.levelname == 'CRITICAL':
                formatted_msg = f"{colored_level} {colored_time} {colored_name} {self.RED}{message}{self.RESET}"
            elif record.levelname == 'WARNING':
                formatted_msg = f"{colored_level} {colored_time} {colored_name} {self.YELLOW}{message}{self.RESET}"
            else:
                formatted_msg = f"{colored_level} {colored_time} {colored_name} {message}"
        
        # Add exception info if present
        if record.exc_info:
            formatted_msg += "\n" + self.formatException(record.exc_info)
        
        return formatted_msg


def setup_logging():
    """Configure clean, readable logging."""
    
    # Set to INFO by default, DEBUG only for your bot modules
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    
    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    
    formatter = CleanFormatter(
        datefmt='%H:%M:%S'
    )
    
    console_handler.setFormatter(formatter)
    console_handler.addFilter(SensitiveDataFilter())
    root_logger.addHandler(console_handler)
    
    # Silence noisy libraries
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("privy").setLevel(logging.WARNING)
    
    # Keep your bot logs at INFO level
    logging.getLogger("bot").setLevel(logging.INFO)
    
    return logging.getLogger(__name__)


logger = setup_logging()

# Global shutdown event - signals run_polling/run_webhook to exit cleanly
shutdown_event = asyncio.Event()


async def main():
    """Main entry point."""
    bot = TagwiseBot()

    # Setup signal handlers - just set the event, don't cancel tasks
    loop = asyncio.get_event_loop()
    
    def _signal_handler():
        if shutdown_event.is_set():
            # Second signal - force exit
            logger.warning("🛑 Forced shutdown (second signal)")
            loop.stop()
            return
        logger.info("🛑 Shutdown signal received, stopping gracefully...")
        shutdown_event.set()
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        mode = "🌐 PRODUCTION (webhook)" if Config.ENV == "production" else "💻 DEVELOPMENT (polling)"
        logger.info("=" * 80)
        logger.info(f"🚀 Starting Tagwise Bot | Mode: {mode}")
        logger.info("=" * 80)
        
        if Config.ENV == "production":
            await bot.run_webhook(
                webhook_url=Config.WEBHOOK_URL,
                port=Config.WEBHOOK_PORT,
                shutdown_event=shutdown_event
            )
        else:
            await bot.run_polling(shutdown_event=shutdown_event)
            
    except KeyboardInterrupt:
        pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.critical(f"❌ Fatal error: {e}", exc_info=True)
        raise
    finally:
        logger.info("✅ Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
