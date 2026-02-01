import httpx
from typing import Optional, Dict, Any

from app.adapters.base import BrokerAdapter, StatusUpdateCallable
from app.models import Order, OrderStatus, TradeOrder
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
            status = OrderStatus.PLACED
            if broker_order.get("status") == "filled":
                status = OrderStatus.EXECUTED

            update_data = {
                "order_id": order.order_id,
                "status": status,
                "broker_order_id": broker_order["id"],
            }
            if broker_order.get("filled_at"):
                update_data["executed_at"] = broker_order["filled_at"]

            await update_callback(update_data)

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
        # Placeholder implementation
        return {"status": "error", "message": "Not implemented"}

    async def get_order_status(self, broker_order_id: str) -> dict:
        # Placeholder implementation
        return {"status": "error", "message": "Not implemented"}

    async def execute(self, trade_order: TradeOrder) -> Dict[str, Any]:
        """
        Executes a trade directly with Alpaca.
        """
        headers = self._get_auth_headers()
        headers["Content-Type"] = "application/json"

        payload = {
            "side": trade_order.side.value,
            "symbol": trade_order.symbol,
            "qty": str(trade_order.quantity),
            "type": trade_order.order_type.value,
            "time_in_force": "gtc",
        }
        url = f"{settings.ALPACA_API_URL}/v2/orders"

        try:
            response = await self._client.post(url, headers=headers, json=payload)
            if response.is_error:
                return {
                    "status": OrderStatus.FAILED,
                    "reason": f"Alpaca API error: {response.text}",
                    "status_code": response.status_code
                }

            broker_order = response.json()
            status = OrderStatus.PLACED
            if broker_order.get("status") == "filled":
                status = OrderStatus.EXECUTED

            result = {
                "status": status,
                "broker_order_id": broker_order["id"],
                "symbol": trade_order.symbol,
                "side": trade_order.side
            }
            if broker_order.get("filled_at"):
                result["executed_at"] = broker_order["filled_at"]

            return result
        except httpx.RequestError as e:
            logger.error("Failed to send request to Alpaca.", extra={"error": str(e)})
            return {
                "status": OrderStatus.FAILED,
                "reason": f"Request failed: {str(e)}"
            }

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
