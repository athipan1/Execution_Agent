import httpx
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.adapters.base import BrokerAdapter, StatusUpdateCallable
from app.models import Order, OrderStatus
from app.config import settings
from app.logging import get_logger

logger = get_logger(__name__)

class TokenError(Exception):
    """Custom exception for token-related errors."""
    pass


class AlpacaAdapter(BrokerAdapter):
    """
    Broker adapter for interacting with the Alpaca Broker API.
    """
    AUTH_URL = "https://authx.alpaca.markets/v1/oauth2/token"

    def __init__(self):
        self._client = httpx.AsyncClient()
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = settings.ALPACA_REFRESH_TOKEN
        self._token_expires_at: Optional[datetime] = None

    async def get_access_token(
        self,
        grant_type: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        code: Optional[str] = None,
        refresh_token: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ) -> dict:
        """
        Authenticates with Alpaca to get an OAuth2 access token.
        Supports both authorization_code and refresh_token grant types.
        """
        client_id = client_id or settings.ALPACA_CLIENT_ID
        client_secret = client_secret or settings.ALPACA_CLIENT_SECRET

        if not client_id or not client_secret:
            logger.error("Alpaca client ID or secret is not configured.")
            raise ValueError("Client ID and Client Secret must be provided.")

        data = {
            "grant_type": grant_type,
            "client_id": client_id,
            "client_secret": client_secret,
        }

        if grant_type == "authorization_code":
            code = code or settings.ALPACA_AUTHORIZATION_CODE
            redirect_uri = redirect_uri or settings.ALPACA_REDIRECT_URI
            if not code:
                raise ValueError("Authorization code must be provided for grant_type 'authorization_code'.")
            data["code"] = code
            data["redirect_uri"] = redirect_uri
        elif grant_type == "refresh_token":
            refresh_token = refresh_token or self._refresh_token
            if not refresh_token:
                raise ValueError("Refresh token must be provided for grant_type 'refresh_token'.")
            data["refresh_token"] = refresh_token
        else:
            raise ValueError(f"Unsupported grant_type: {grant_type}")

        try:
            response = await self._client.post(self.AUTH_URL, data=data)
            response.raise_for_status()
            token_data = response.json()

            if "access_token" not in token_data:
                raise TokenError("Missing access_token in response")

            self._access_token = token_data["access_token"]
            # refresh_token is not always returned, especially on subsequent refreshes
            if "refresh_token" in token_data:
                self._refresh_token = token_data["refresh_token"]

            expires_in = token_data.get("expires_in", 3600)
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 30)

            logger.info(f"Successfully obtained new Alpaca access token using grant_type '{grant_type}'.")
            return {"access_token": self._access_token, "refresh_token": self._refresh_token}

        except httpx.RequestError as e:
            logger.error("Failed to send request to Alpaca.", extra={"error": str(e)})
            raise
        except httpx.HTTPStatusError as e:
            logger.error(
                "Failed to get Alpaca access token.",
                extra={"status_code": e.response.status_code, "response": e.response.text},
            )
            raise

    async def _get_valid_access_token(self) -> Optional[str]:
        """
        Returns a valid access token, refreshing it if necessary.
        """
        if self._access_token and self._token_expires_at and self._token_expires_at > datetime.now(timezone.utc):
            return self._access_token

        if self._refresh_token:
            logger.info("Access token is expired or missing, attempting to refresh.")
            try:
                await self.get_access_token(grant_type="refresh_token")
                return self._access_token
            except (ValueError, httpx.RequestError, httpx.HTTPStatusError, TokenError) as e:
                logger.error(f"Failed to refresh access token: {e}")
                return None
        else:
            logger.warning("No refresh token available. Cannot refresh access token.")
            return None


    async def place_order(self, order: Order, update_callback: StatusUpdateCallable):
        """
        Places a market order with Alpaca and handles the response.
        """
        token = await self._get_valid_access_token()
        if not token:
            await update_callback({
                "order_id": order.order_id,
                "status": OrderStatus.FAILED,
                "reason": "Authentication failed: Could not get a valid access token.",
            })
            return

        response = await self._make_order_request(order, token)

        # A 401 could still happen if the token was revoked server-side.
        if response and response.status_code == 401:
            logger.warning("Access token was rejected by broker API. Forcing a refresh.")
            self._access_token = None # Invalidate the token
            token = await self._get_valid_access_token()
            if not token:
                await update_callback({
                    "order_id": order.order_id,
                    "status": OrderStatus.FAILED,
                    "reason": "Authentication failed: Could not refresh access token after rejection.",
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
        Verifies the connection to Alpaca by getting a valid token
        and making a request to the /v2/account endpoint.
        Returns True if successful, False otherwise.
        """
        logger.info("Checking connection to Alpaca...")
        token = await self._get_valid_access_token()
        if not token:
            logger.error("Alpaca connection check failed: Could not obtain a valid access token.")
            return False

        headers = {"Authorization": f"Bearer {token}"}
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
