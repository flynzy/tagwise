"""NOWPayments gateway client for Tagwise PRO subscriptions."""

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Optional

import aiohttp

from bot.config import NOWPaymentsConfig, TierLimits

logger = logging.getLogger(__name__)

NOWPAYMENTS_API = "https://api.nowpayments.io/v1"


class NOWPaymentsService:
    """Creates NOWPayments invoices and verifies IPN webhooks."""

    def __init__(self):
        self.api_key = NOWPaymentsConfig.API_KEY
        self.ipn_secret = NOWPaymentsConfig.IPN_SECRET

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    async def create_payment(
        self,
        user_id: int,
        plan: str,
        return_url: str = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Create a NOWPayments invoice for a Tagwise PRO subscription.

        Returns dict with payment_url, order_id, amount, plan on success.
        Returns None on failure.
        """
        try:
            if plan == "annual":
                amount = TierLimits.PRO_PRICE_ANNUAL
                description = "Tagwise PRO Annual"
            else:
                amount = TierLimits.PRO_PRICE_MONTHLY
                description = "Tagwise PRO Monthly"

            order_id = f"tagwise_{user_id}_{plan}_{int(time.time())}"

            if not return_url:
                return_url = "https://t.me/tagwise_bot"

            callback_url = NOWPaymentsConfig.get_callback_url()

            body = {
                "price_amount": amount,
                "price_currency": "usd",
                "order_id": order_id,
                "order_description": description,
                "ipn_callback_url": callback_url,
                "success_url": return_url,
                "cancel_url": return_url,
                "is_fixed_rate": True,
                "is_fee_paid_by_user": False,
            }

            logger.info("=== NOWPAYMENTS DEBUG ===")
            logger.info(f"API Key present: {bool(self.api_key)}")
            logger.info(f"Callback URL: {callback_url}")
            logger.info(f"Order ID: {order_id}")
            logger.info(f"Amount: {amount} USD ({plan})")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{NOWPAYMENTS_API}/invoice",
                    json=body,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    response_text = await response.text()
                    logger.info(f"Response Status: {response.status}")
                    logger.info(f"Response Body: {response_text[:500]}")

                    if response.status in (200, 201):
                        data = json.loads(response_text)
                        payment_url = data.get("invoice_url")

                        if payment_url:
                            logger.info(f"✅ NOWPayments invoice created: {payment_url}")
                            return {
                                "payment_url": payment_url,
                                "uuid": data.get("id"),
                                "order_id": order_id,
                                "amount": amount,
                                "plan": plan,
                            }
                        else:
                            logger.error(f"No invoice_url in NOWPayments response: {data}")
                            return None
                    else:
                        logger.error(f"NOWPayments API error: {response.status} - {response_text}")
                        return None

        except Exception as e:
            logger.error(f"Error creating NOWPayments invoice: {e}", exc_info=True)
            return None

    def verify_webhook_signature(self, body_data: dict, received_sign: str) -> bool:
        """
        Verify NOWPayments IPN webhook signature.

        NOWPayments signs with HMAC-SHA512 of the sorted JSON body
        using the IPN secret as the key.
        """
        if not self.ipn_secret:
            logger.warning("No IPN secret configured, skipping signature verification")
            return True

        try:
            # Sort keys, compact JSON — NOWPayments requirement
            sorted_body = json.dumps(body_data, sort_keys=True, separators=(",", ":"))
            computed = hmac.new(
                self.ipn_secret.encode(),
                sorted_body.encode(),
                hashlib.sha512,
            ).hexdigest()

            if hmac.compare_digest(computed, received_sign.lower()):
                return True
            else:
                logger.warning(
                    f"Signature mismatch: computed={computed[:20]}..., "
                    f"received={received_sign[:20]}..."
                )
                return False

        except Exception as e:
            logger.error(f"Signature verification error: {e}")
            return False

    @staticmethod
    def parse_order_id(order_id: str) -> Optional[Dict[str, Any]]:
        """
        Parse order_id to extract user_id and plan.
        Format: tagwise_{user_id}_{plan}_{timestamp}
        """
        try:
            parts = order_id.split("_")
            if len(parts) >= 3 and parts[0] == "tagwise":
                return {
                    "user_id": int(parts[1]),
                    "plan": parts[2],
                }
        except Exception as e:
            logger.error(f"Error parsing order_id '{order_id}': {e}")
        return None


# Global instance
nowpayments_service = NOWPaymentsService()
