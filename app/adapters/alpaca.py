import httpx
from typing import Optional

from app.adapters.base import BrokerAdapter, StatusUpdateCallable
from app.models import Order, OrderStatus
from app.config import settings
from app.logging import get_logger

logger = get_logger(__name__)


class AlpacaAdapter(BrokerAdapter):
    """
    Broker adapter for interacting with the Alpaca Broker API using API Keys.
    """

    def __init__(self):
        self._client = httpx.AsyncClient()
        if not settings.ALPACA_API_KEY_ID or not settings.ALPACA_SECRET_KEY:
            logger.error("Alpaca API Key ID or Secret Key is not configured.")
            raise ValueError("ALPACA_API_KEY_ID and ALPACA_SECRET_KEY must be configured.")

    def _get_auth_headers(self) -> dict:
        """Returns the authentication headers for Alpaca API requests."""
        return {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY_ID,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        }

    async def place_order(self, order: Order, update_callback: StatusUpdateCallable):
        """
        Places a market order with Alpaca and handles the response.
        """
        response = await self._make_order_request(order)

        if not response or response.is_error:
            reason = f"Broker API request failed with status {response.status_code if response else 'N/A'}"
            if response:
                reason += f": {response.text}"
            await update_callback({
                "order_id": order.order_id,
                "status": OrderStatus.FAILED,
                "reason": reason,
            })
        else:
            broker_order = response.json()
            await update_callback({
                "order_id": order.order_id,
                "status": OrderStatus.PLACED,
                "broker_order_id": broker_order["id"],
            })

    async def _make_order_request(self, order: Order) -> Optional[httpx.Response]:
        """
        Helper method to make the actual HTTP request to place an order.
        """
        headers = self._get_auth_headers()
        headers["Content-Type"] = "application/json"

        payload = {
            "side": order.side.value,
            "symbol": order.symbol,
            "qty": str(order.quantity),
            "type": "market",
            "time_in_force": "gtc",
        }
        # Use the v2 endpoint for placing orders
        url = f"{settings.ALPACA_API_URL}/v2/orders"

        try:
            response = await self._client.post(url, headers=headers, json=payload)
            return response
        except httpx.RequestError as e:
            logger.error("Failed to send request to Alpaca.", extra={"error": str(e)})
            return None

    async def cancel_order(self, broker_order_id: str) -> dict:
        """
        Cancels a specific order with Alpaca.
        """
        headers = self._get_auth_headers()
        url = f"{settings.ALPACA_API_URL}/v2/orders/{broker_order_id}"

        try:
            response = await self._client.delete(url, headers=headers)
            response.raise_for_status()

            # A successful cancellation returns a 204 No Content status.
            # If the order is already cancelled, filled, or expired, Alpaca might return a 422 or 404.
            if response.status_code == 204:
                logger.info(f"Successfully cancelled order {broker_order_id}.")
                return {"status": OrderStatus.CANCELLED.value}
            else:
                # This case might not be reached if raise_for_status() covers all non-2xx codes
                logger.warning(
                    f"Received unexpected status code {response.status_code} when cancelling order {broker_order_id}.",
                    extra={"response": response.text}
                )
                return {"status": "error", "message": "Unexpected status code from broker."}

        except httpx.RequestError as e:
            logger.error(f"Failed to send cancellation request to Alpaca for order {broker_order_id}.", extra={"error": str(e)})
            return {"status": "error", "message": "Request to broker failed."}
        except httpx.HTTPStatusError as e:
            # Handle cases where the order cannot be cancelled (e.g., already filled)
            if e.response.status_code in [404, 422]:
                 logger.warning(
                    f"Order {broker_order_id} could not be cancelled. It might be already filled, expired or cancelled.",
                    extra={"status_code": e.response.status_code, "response": e.response.text},
                )
                 return {"status": "error", "message": f"Order cannot be cancelled: {e.response.json().get('message')}"}
            else:
                logger.error(
                    f"HTTP error when cancelling order {broker_order_id}.",
                    extra={"status_code": e.response.status_code, "response": e.response.text},
                )
                return {"status": "error", "message": "HTTP error from broker."}

    async def get_order_status(self, broker_order_id: str) -> dict:
        # Placeholder implementation
        return {"status": "error", "message": "Not implemented"}

    async def check_connection(self) -> bool:
        """
        Verifies the connection to Alpaca by making a request to the /v2/account endpoint.
        Returns True if successful, False otherwise.
        """
        logger.info("Checking connection to Alpaca...")
        headers = self._get_auth_headers()
        url = f"{settings.ALPACA_API_URL}/v2/account"

        try:
            response = await self._client.get(url, headers=headers)
            response.raise_for_status()
            logger.info("Alpaca connection check successful. Account details retrieved.")
            return True
        except httpx.RequestError as e:
            logger.error("Alpaca connection check failed: Could not connect to account endpoint.", extra={"error": str(e)})
            return False
        except httpx.HTTPStatusError as e:
            logger.error(
                "Alpaca connection check failed: Invalid response from account endpoint.",
                extra={"status_code": e.response.status_code, "response": e.response.text},
            )
            return False

    async def health_check_place_and_cancel_order(self, api_key: str, secret_key: str) -> dict:
        """
        Performs a health check by placing and immediately cancelling an order.
        Uses the provided API key and secret for this specific operation.
        """
        log_extra = {"operation": "health_check_place_and_cancel_order"}
        logger.info("Starting Alpaca health check: place and cancel order.", extra=log_extra)

        # --- Place Order ---
        order_headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Content-Type": "application/json",
        }
        order_payload = {
            "side": "buy",
            "symbol": "SPY",
            "qty": "1",
            "type": "market",
            "time_in_force": "gtc",
        }
        order_url = f"{settings.ALPACA_API_URL}/v2/orders"
        broker_order_id = None

        try:
            logger.info("Attempting to place a test order for SPY.", extra=log_extra)
            response = await self._client.post(order_url, headers=order_headers, json=order_payload)
            response.raise_for_status()
            order_data = response.json()
            broker_order_id = order_data.get("id")
            if not broker_order_id:
                logger.error("Order creation failed: Broker did not return an order ID.", extra=log_extra)
                return {"status": "error", "message": "Order creation failed, no order ID returned."}
            logger.info(f"Test order placed successfully. Broker Order ID: {broker_order_id}", extra=log_extra)

        except httpx.HTTPStatusError as e:
            logger.error(
                "HTTP error during order placement.",
                extra={**log_extra, "status_code": e.response.status_code, "response": e.response.text},
            )
            return {"status": "error", "creation": "failed", "cancellation": "skipped", "reason": e.response.text}
        except Exception as e:
            logger.error("An unexpected error occurred during order placement.", extra={**log_extra, "error": str(e)})
            return {"status": "error", "creation": "failed", "cancellation": "skipped", "reason": str(e)}

        # --- Cancel Order ---
        cancel_headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }
        cancel_url = f"{settings.ALPACA_API_URL}/v2/orders/{broker_order_id}"
        try:
            logger.info(f"Attempting to cancel test order {broker_order_id}.", extra=log_extra)
            response = await self._client.delete(cancel_url, headers=cancel_headers)
            response.raise_for_status()
            logger.info(f"Test order {broker_order_id} cancelled successfully.", extra=log_extra)

            return {
                "status": "ok",
                "order_id": broker_order_id,
                "creation": "success",
                "cancellation": "success",
            }
        except httpx.HTTPStatusError as e:
            # A 422 or 404 can happen if the market order fills instantly
            if e.response.status_code in [422, 404]:
                logger.warning(
                    f"Order {broker_order_id} could not be cancelled, likely because it was filled instantly.",
                    extra={**log_extra, "response": e.response.text}
                )
                return {
                    "status": "ok",
                    "order_id": broker_order_id,
                    "creation": "success",
                    "cancellation": "not_cancelled_or_already_filled",
                    "reason": e.response.text
                }
            logger.error(
                f"HTTP error during order cancellation for {broker_order_id}.",
                extra={**log_extra, "status_code": e.response.status_code, "response": e.response.text},
            )
            return {
                "status": "error",
                "order_id": broker_order_id,
                "creation": "success",
                "cancellation": "failed",
                "reason": e.response.text
            }
        except Exception as e:
            logger.error(f"An unexpected error occurred during order cancellation for {broker_order_id}.", extra={**log_extra, "error": str(e)})
            return {
                "status": "error",
                "order_id": broker_order_id,
                "creation": "success",
                "cancellation": "failed",
                "reason": str(e)
            }
