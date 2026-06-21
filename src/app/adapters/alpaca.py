import httpx
from typing import Optional, Dict, Any, List

from app.adapters.base import BrokerAdapter, StatusUpdateCallable
from app.models import Order, OrderStatus, TradeOrder
from app.config import settings
from app.logging import get_logger
from app.services.protective_order_service import (
    ProtectiveOrderError,
    build_alpaca_entry_payload,
)

logger = get_logger(__name__)


def _clean_secret(value: str | None) -> str:
    """Remove accidental whitespace/newlines from secret values before using them in HTTP headers."""
    return (value or "").strip()


def _normalize_base_url(url: str) -> str:
    """Return Alpaca API base URL without trailing slash or duplicate /v2."""
    base_url = _clean_secret(url) or "https://paper-api.alpaca.markets"
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v2"):
        base_url = base_url[:-3]
    return base_url


class AlpacaAdapter(BrokerAdapter):
    """
    Broker adapter for interacting with Alpaca Trading API using paper/live API keys.
    """

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        self.base_url = _normalize_base_url(settings.ALPACA_API_URL)
        self.api_key_id = _clean_secret(settings.ALPACA_API_KEY_ID)
        self.secret_key = _clean_secret(settings.ALPACA_SECRET_KEY)
        if not self.api_key_id or not self.secret_key:
            logger.error("Alpaca API Key ID or Secret Key is not configured.")
            raise ValueError("ALPACA_API_KEY_ID and ALPACA_SECRET_KEY must be configured.")

    def _get_auth_headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.api_key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

    def _url(self, path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}{normalized_path}"

    async def _get_json(self, path: str) -> Any:
        headers = self._get_auth_headers()
        response = await self._client.get(self._url(path), headers=headers)
        response.raise_for_status()
        return response.json()

    async def place_order(self, order: Order, update_callback: StatusUpdateCallable):
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
            update_data = self._map_alpaca_order_to_internal(broker_order)
            update_data["order_id"] = order.order_id
            await update_callback(update_data)

    async def _make_order_request(self, order: Order) -> Optional[httpx.Response]:
        headers = self._get_auth_headers()
        headers["Content-Type"] = "application/json"

        try:
            payload = build_alpaca_entry_payload(
                order,
                require_protection=str(settings.TRADING_MODE or "PAPER").upper() == "LIVE",
            )
        except ProtectiveOrderError as exc:
            logger.error(
                "Refusing to submit unprotected or invalid Alpaca order.",
                extra={"order_id": order.order_id, "symbol": order.symbol, "error": str(exc)},
            )
            return httpx.Response(422, json={"message": str(exc)})

        try:
            return await self._client.post(self._url("/v2/orders"), headers=headers, json=payload)
        except httpx.RequestError as e:
            logger.error("Failed to send request to Alpaca.", extra={"error": str(e)})
            return None

    async def cancel_order(self, broker_order_id: str) -> dict:
        headers = self._get_auth_headers()

        try:
            response = await self._client.delete(self._url(f"/v2/orders/{broker_order_id}"), headers=headers)
            if response.status_code == 204:
                return {"status": OrderStatus.CANCELLED}
            logger.error(
                "Failed to cancel Alpaca order.",
                extra={"broker_order_id": broker_order_id, "status_code": response.status_code, "response": response.text}
            )
            return {"status": "error", "message": f"Alpaca API error: {response.text}"}
        except httpx.RequestError as e:
            logger.error("Request error while cancelling Alpaca order.", extra={"error": str(e)})
            return {"status": "error", "message": str(e)}

    async def get_order_status(self, broker_order_id: str) -> dict:
        headers = self._get_auth_headers()

        try:
            response = await self._client.get(self._url(f"/v2/orders/{broker_order_id}"), headers=headers)
            if response.is_error:
                return {"status": "error", "message": f"Alpaca API error: {response.text}"}

            broker_order = response.json()
            return self._map_alpaca_order_to_internal(broker_order)
        except httpx.RequestError as e:
            logger.error("Request error while fetching Alpaca order status.", extra={"error": str(e)})
            return {"status": "error", "message": str(e)}

    def _map_alpaca_order_to_internal(self, alpaca_order: dict) -> dict:
        status_map = {
            "new": OrderStatus.PLACED,
            "accepted": OrderStatus.PLACED,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "filled": OrderStatus.EXECUTED,
            "canceled": OrderStatus.CANCELLED,
            "expired": OrderStatus.FAILED,
            "rejected": OrderStatus.FAILED,
            "pending_cancel": OrderStatus.PLACED,
            "pending_replace": OrderStatus.PLACED,
        }

        alpaca_status = alpaca_order.get("status")
        internal_status = status_map.get(alpaca_status, OrderStatus.FAILED)

        update_data = {
            "status": internal_status,
            "broker_order_id": alpaca_order["id"],
            "executed_quantity": int(float(alpaca_order.get("filled_qty", 0) or 0)),
        }

        if alpaca_order.get("filled_avg_price"):
            update_data["avg_execution_price"] = float(alpaca_order["filled_avg_price"])

        if alpaca_order.get("filled_at"):
            update_data["executed_at"] = alpaca_order["filled_at"]

        if alpaca_status in {"rejected", "expired"}:
            update_data["reason"] = f"Alpaca {alpaca_status}: {alpaca_order.get('failed_reason', 'Unknown reason')}"

        return update_data

    async def execute(self, trade_order: TradeOrder) -> Dict[str, Any]:
        headers = self._get_auth_headers()
        headers["Content-Type"] = "application/json"

        payload = {
            "side": trade_order.side.value,
            "symbol": trade_order.symbol,
            "qty": str(trade_order.quantity),
            "type": trade_order.order_type.value,
            "time_in_force": "gtc",
        }

        try:
            response = await self._client.post(self._url("/v2/orders"), headers=headers, json=payload)
            if response.is_error:
                return {
                    "status": OrderStatus.FAILED,
                    "reason": f"Alpaca API error: {response.text}",
                    "status_code": response.status_code
                }

            broker_order = response.json()
            return self._map_alpaca_order_to_internal(broker_order)
        except httpx.RequestError as e:
            logger.error("Failed to send request to Alpaca.", extra={"error": str(e)})
            return {
                "status": OrderStatus.FAILED,
                "reason": f"Request failed: {str(e)}"
            }

    async def get_account(self) -> Dict[str, Any]:
        account = await self._get_json("/v2/account")
        return {
            "broker": "ALPACA",
            "paper": "paper-api.alpaca.markets" in self.base_url,
            "account_id": account.get("id"),
            "status": account.get("status"),
            "currency": account.get("currency"),
            "cash": account.get("cash"),
            "buying_power": account.get("buying_power"),
            "equity": account.get("equity"),
            "portfolio_value": account.get("portfolio_value"),
            "pattern_day_trader": account.get("pattern_day_trader"),
            "trading_blocked": account.get("trading_blocked"),
            "transfers_blocked": account.get("transfers_blocked"),
            "account_blocked": account.get("account_blocked"),
        }

    async def get_positions(self) -> List[Dict[str, Any]]:
        positions = await self._get_json("/v2/positions")
        return [
            {
                "symbol": item.get("symbol"),
                "qty": item.get("qty"),
                "side": item.get("side"),
                "market_value": item.get("market_value"),
                "avg_entry_price": item.get("avg_entry_price"),
                "current_price": item.get("current_price"),
                "unrealized_pl": item.get("unrealized_pl"),
                "unrealized_plpc": item.get("unrealized_plpc"),
                "asset_class": item.get("asset_class"),
                "exchange": item.get("exchange"),
            }
            for item in positions
        ]

    async def get_open_orders(self) -> List[Dict[str, Any]]:
        orders = await self._get_json("/v2/orders?status=open&limit=100")
        return [
            {
                "id": item.get("id"),
                "symbol": item.get("symbol"),
                "side": item.get("side"),
                "qty": item.get("qty"),
                "filled_qty": item.get("filled_qty"),
                "type": item.get("type"),
                "time_in_force": item.get("time_in_force"),
                "status": item.get("status"),
                "submitted_at": item.get("submitted_at"),
                "limit_price": item.get("limit_price"),
                "stop_price": item.get("stop_price"),
            }
            for item in orders
        ]

    async def check_connection(self) -> bool:
        logger.info("Checking connection to Alpaca...")
        try:
            await self.get_account()
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
