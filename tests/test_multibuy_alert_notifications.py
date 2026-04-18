import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.notifications import NotificationService


def _build_service(notification_queue=None, users=None):
    db = AsyncMock()
    db.get_users_with_multibuy_alerts = AsyncMock(return_value=users or [111, 222])
    copy_manager = MagicMock()
    return NotificationService(db=db, copy_manager=copy_manager, notification_queue=notification_queue)


def _build_context():
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


def test_send_multibuy_alerts_uses_notification_queue_when_available():
    queue = AsyncMock()
    service = _build_service(notification_queue=queue)
    context = _build_context()

    with patch("bot.services.notifications.asyncio.sleep", new=AsyncMock()):
        asyncio.run(service._send_multibuy_alerts(
            market_id="m1",
            market_title="Bitcoin & Ethereum > Solana?",
            outcome="YES",
            wallet_addresses=["0xabc", "0xdef"],
            recent_buys=[
                {"wallet_address": "0xabc", "usdc_size": 15},
                {"wallet_address": "0xdef", "usdc_size": 20},
            ],
            context=context,
        ))

    assert queue.enqueue.await_count == 2
    first_call_kwargs = queue.enqueue.await_args_list[0].kwargs
    assert first_call_kwargs["priority"] == 6
    assert first_call_kwargs["user_id"] == 111
    assert "**Multi-Buy Alert!**" in first_call_kwargs["message"]
    assert "<b>" not in first_call_kwargs["message"]
    context.bot.send_message.assert_not_awaited()


def test_send_multibuy_alerts_falls_back_to_markdown_send_message():
    service = _build_service(notification_queue=None, users=[333])
    context = _build_context()
    market_title = "Will inflation be < 2% & GDP > 3%?"

    with patch("bot.services.notifications.asyncio.sleep", new=AsyncMock()):
        asyncio.run(service._send_multibuy_alerts(
            market_id="m2",
            market_title=market_title,
            outcome="NO",
            wallet_addresses=["0xabc", "0xdef"],
            recent_buys=[
                {"wallet_address": "0xabc", "usdc_size": 12.5},
                {"wallet_address": "0xdef", "usdc_size": 8.0},
            ],
            context=context,
        ))

    context.bot.send_message.assert_awaited_once()
    call_kwargs = context.bot.send_message.await_args.kwargs
    assert call_kwargs["chat_id"] == 333
    assert call_kwargs["parse_mode"] == "Markdown"
    assert "**Market:**" in call_kwargs["text"]
    assert market_title in call_kwargs["text"]
    assert "<b>" not in call_kwargs["text"]
