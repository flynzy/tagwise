# bot/monitoring.py
"""Monitoring and metrics collection."""

import time
import logging
from functools import wraps

try:
    from prometheus_client import Counter, Histogram, Gauge, start_http_server
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("prometheus_client not installed. Metrics disabled.")

logger = logging.getLogger(__name__)

# Metrics
REQUESTS_TOTAL = Counter(
    'bot_requests_total', 
    'Total requests processed',
    ['command', 'status']
) if PROMETHEUS_AVAILABLE else None

REQUEST_DURATION = Histogram(
    'bot_request_duration_seconds',
    'Request duration in seconds',
    ['command']
) if PROMETHEUS_AVAILABLE else None

ACTIVE_USERS = Gauge(
    'bot_active_users',
    'Number of active users'
) if PROMETHEUS_AVAILABLE else None

TRACKED_WALLETS = Gauge(
    'bot_tracked_wallets_total',
    'Total wallets being tracked'
) if PROMETHEUS_AVAILABLE else None

POLYMARKET_API_CALLS = Counter(
    'polymarket_api_calls_total',
    'Total Polymarket API calls',
    ['endpoint', 'status']
) if PROMETHEUS_AVAILABLE else None


def track_command(command_name: str):
    """Decorator to track command execution metrics."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"
            
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                raise
            finally:
                duration = time.time() - start_time
                if PROMETHEUS_AVAILABLE:
                    REQUESTS_TOTAL.labels(command=command_name, status=status).inc()
                    REQUEST_DURATION.labels(command=command_name).observe(duration)
        
        return wrapper
    return decorator


def start_metrics_server(port: int = 9090):
    """Start Prometheus metrics server."""
    if not PROMETHEUS_AVAILABLE:
        return
    start_http_server(port)
    logger.info(f"Metrics server started on port {port}")
