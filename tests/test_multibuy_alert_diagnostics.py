import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.services.notifications import NotificationService


@pytest.mark.asyncio
async def test_send_multibuy_alerts_logs_when_no_eligible_users(caplog):
    db = AsyncMock()
    db.get_users_with_multibuy_alerts = AsyncMock(return_value=[])
    copy_manager = AsyncMock()
    service = NotificationService(db=db, copy_manager=copy_manager, notification_queue=None)

    context = MagicMock()
    context.bot.send_message = AsyncMock()

    with caplog.at_level(logging.WARNING):
        await service._send_multibuy_alerts(
            market_id="market-1",
            market_title="Test Market",
            outcome="NO",
            wallet_addresses=["0xabc", "0xdef"],
            recent_buys=[],
            context=context,
        )

    assert "get_users_with_multibuy_alerts returned 0 users" in caplog.text
    context.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_multibuy_dedup_skip_logged_at_info(caplog):
    db = AsyncMock()
    db.record_buy_for_multibuy = AsyncMock(return_value=True)
    db.get_multibuy_wallets = AsyncMock(return_value=["0xabc", "0xdef"])
    db.get_recent_buys_for_market = AsyncMock(
        return_value=[
            {"wallet_address": "0xabc", "usdc_size": 100.0},
            {"wallet_address": "0xdef", "usdc_size": 120.0},
        ]
    )
    db.get_users_with_multibuy_alerts = AsyncMock(return_value=[])

    copy_manager = AsyncMock()
    copy_manager.process_multibuy_copy_trades = AsyncMock(return_value=[])

    service = NotificationService(db=db, copy_manager=copy_manager, notification_queue=None)
    context = MagicMock()
    context.bot.send_message = AsyncMock()

    trade = {
        "side": "BUY",
        "condition_id": "market-1",
        "outcome": "NO",
        "title": "Test Market",
    }

    await service.check_and_process_multibuy(trade, "0xabc", context)

    with caplog.at_level(logging.INFO):
        await service.check_and_process_multibuy(trade, "0xdef", context)

    assert "Multi-buy already processed - skipping" in caplog.text
