"""Webhook handlers for external services."""

import json
import logging
import os

from aiohttp import web

from bot.nowpayments import nowpayments_service

logger = logging.getLogger(__name__)


class WebhookService:
    """Handles webhook endpoints for external services."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.app = None

    async def start_webhook_server(self):
        """Start the webhook server for NOWPayments callbacks."""
        self.app = web.Application()

        self.app.router.add_post('/webhook/nowpayments', self.handle_nowpayments_webhook)
        self.app.router.add_get('/health', lambda r: web.Response(text="OK"))

        runner = web.AppRunner(self.app)
        await runner.setup()

        port = int(os.getenv('NOWPAYMENTS_WEBHOOK_PORT', 8081))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()

        logger.info(f"✅ Webhook server running on port {port}")

    async def handle_nowpayments_webhook(self, request: web.Request) -> web.Response:
        """Handle NOWPayments IPN callbacks when payment status changes."""
        try:
            raw_body = await request.read()
            body_str = raw_body.decode('utf-8')

            logger.info(f"NOWPayments webhook received: {body_str[:500]}")
            logger.info(f"Webhook headers: {dict(request.headers)}")

            # NOWPayments IPN whitelist (optional hardening)
            # NOWPayments sends from varying IPs so IP whitelisting is not recommended
            # Rely on HMAC-SHA512 signature verification instead.

            try:
                data = json.loads(body_str)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in webhook: {e}")
                return web.Response(status=400, text="Invalid JSON")

            # --- Verify signature ---
            # NOWPayments sends signature in the header, NOT in the body
            received_sign = request.headers.get("x-nowpayments-sig", "")
            if not received_sign:
                logger.warning("No x-nowpayments-sig header in webhook")
                return web.Response(status=401, text="Missing signature")

            if not nowpayments_service.verify_webhook_signature(data, received_sign):
                logger.warning("Invalid NOWPayments webhook signature")
                return web.Response(status=401, text="Invalid signature")

            logger.info(f"Parsed webhook data: {data}")

            # --- Check payment status ---
            status = data.get("payment_status", "")

            # NOWPayments confirmed statuses that should activate PRO
            PAID_STATUSES = {"finished", "confirmed"}
            if status not in PAID_STATUSES:
                logger.info(f"Ignoring webhook with status: {status}")
                return web.Response(text="OK")

            # --- Extract order info ---
            order_id = data.get("order_id")
            if not order_id:
                logger.error("No order_id in webhook")
                return web.Response(status=400, text="Missing order_id")

            order_info = nowpayments_service.parse_order_id(order_id)
            if not order_info:
                logger.error(f"Could not parse order_id: {order_id}")
                return web.Response(status=400, text="Invalid order_id format")

            user_id = order_info["user_id"]
            plan = order_info["plan"]

            # NOWPayments field names for amount and transaction ID
            amount = data.get("price_amount") or data.get("actually_paid") or 0
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                amount = 0

            payment_tx = data.get("payment_id") or data.get("invoice_id") or order_id

            # --- Activate PRO subscription ---
            success = await self.db.upgrade_to_pro(
                user_id=user_id,
                subscription_type=plan,
                payment_method="crypto",
                payment_tx=str(payment_tx),
                payment_amount=amount,
            )

            if success:
                logger.info(f"✅ Activated PRO for user {user_id} ({plan})")

                try:
                    sub_info = await self.db.get_subscription_info(user_id)
                    days = sub_info.get("days_remaining", 30 if plan == "monthly" else 365)

                    await self.bot.app.bot.send_message(
                        chat_id=user_id,
                        text=f"""
🎉 **Welcome to Tagwise PRO!**

Your payment has been confirmed and your **{plan}** subscription is now active!

✅ **What's unlocked:**
• Confidence scores on all alerts
• Unlimited wallet tracking
• Track all top traders
• Advanced leaderboard filters

⏰ **Subscription:** {days} days remaining

Use /account to view your status.
Happy trading! 🚀
""",
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.error(f"Could not send confirmation to user {user_id}: {e}")

                return web.Response(text="OK")
            else:
                logger.error(f"Failed to activate PRO for user {user_id}")
                return web.Response(status=500, text="Activation failed")

        except Exception as e:
            logger.error(f"Webhook error: {e}", exc_info=True)
            return web.Response(status=500, text=str(e))
