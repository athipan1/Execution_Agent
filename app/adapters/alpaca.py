import httpx
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.adapters.base import BrokerAdapter, StatusUpdateCallable
from app.models import Order, OrderStatus
from app.config import settings
from app.logging import get_logger

logger = get_logger(__name__)

class AlpacaAdapter(BrokerAdapter):
    """
    Broker adapter for interacting with the Alpaca Broker API.
    """
    def __init__(self):
        self._client = httpx.AsyncClient()
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    async def _get_access_token(self) -> Optional[str]:
        """
        Authenticates with Alpaca to get an OAuth2 access token.
        """
        if not settings.ALPACA_CLIENT_ID or not settings.ALPACA_CLIENT_SECRET:
            logger.error("Alpaca client ID or secret is not configured.")
            return None

        try:
            response = await self._client.post(
                settings.ALPACA_AUTH_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.ALPACA_CLIENT_ID,
                    "client_secret": settings.ALPACA_CLIENT_SECRET,
                    "scope": "brokerapi:write:orders",
                },
            )
            response.raise_for_status()
            token_data = response.json()
            self._access_token = token_data["access_token"]
            # Set expiry with a 30-second buffer
            expires_in = token_data.get("expires_in", 3600)
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 30)
            logger.info("Successfully obtained new Alpaca access token.")
            return self._access_token
        except httpx.HTTPStatusError as e:
            logger.error(
                "Failed to get Alpaca access token.",
                extra={"status_code": e.response.status_code, "response": e.response.text},
            )
            return None

    async def _get_valid_access_token(self) -> Optional[str]:
        """
        Returns a valid access token, refreshing it if necessary.
        """
        if self._access_token and self._token_expires_at and self._token_expires_at > datetime.now(timezone.utc):
            return self._access_token
        return await self._get_access_token()

    async def place_order(self, order: Order, update_callback: StatusUpdateCallable):
        """
        Places a market order with Alpaca and handles the response.
        """
        token = await self._get_valid_access_token()
        if not token:
            await update_callback({
                "order_id": order.order_id,
                "status": OrderStatus.FAILED,
                "reason": "Authentication failed: Could not get access token.",
            })
            return

        response = await self._make_order_request(order, token)

        if response and response.status_code == 401:
            logger.info("Access token expired. Attempting to refresh and retry.")
            token = await self._get_access_token()  # Force a refresh
            if not token:
                await update_callback({
                    "order_id": order.order_id,
                    "status": OrderStatus.FAILED,
                    "reason": "Authentication failed: Could not refresh access token.",
                })
                return
            response = await self._make_order_request(order, token) # Retry

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

    async def _make_order_request(self, order: Order, token: str) -> Optional[httpx.Response]:
        """
        Helper method to make the actual HTTP request to place an order.
        """
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "side": order.side.value,
            "symbol": order.symbol,
            "qty": str(order.quantity),
            "type": "market",
            "time_in_force": "gtc", # Alpaca requires this for market orders
        }
        url = f"{settings.ALPACA_BROKER_URL}/v1/orders"

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

    async def check_connection(self) -> bool:
        """
        Verifies the connection to Alpaca by attempting to authenticate.
        Returns True if successful, False otherwise.
        """
        logger.info("Checking connection to Alpaca...")
        token = await self._get_valid_access_token()
        if token:
            logger.info("Alpaca connection check successful.")
            return True
        else:
            logger.error("Alpaca connection check failed.")
            return False
