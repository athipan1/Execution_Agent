import httpx

import urllib.parse

from typing import Optional, Dict, Any, List

from app.adapters.base import BrokerAdapter, StatusUpdateCallable

from app.models import Order, OrderStatus, TradeOrder

from app.config import settings

from app.logging import get_logger

from app.services.protective_order_service import (

    ProtectiveOrderError,

    build_alpaca_entry_payload,

    build_alpaca_reduce_only_exit_payload,

    is_profit_lifecycle_exit,

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

    Safety rule:

    - New Alpaca entry orders must be broker-side protected.

    - Both Paper and Live modes require bracket protection: entry + take profit + stop loss.

    - Profit lifecycle exits use Alpaca's position-closing endpoint so they cannot open a short.

    - If Risk/Manager does not provide a valid TP/SL plan, the order is rejected before reaching Alpaca.

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

    async def _post_order_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:

        headers = self._get_auth_headers()

        headers["Content-Type"] = "application/json"

        try:

            response = await self._client.post(

                self._url("/v2/orders"),

                headers=headers,

                json=payload,

            )

            if response.is_error:

                return {

                    "status": OrderStatus.FAILED,

                    "reason": f"Alpaca API error: {response.text}",

                    "status_code": response.status_code,

                    "payload": payload,

                }

            broker_order = response.json()

            mapped = self._map_alpaca_order_to_internal(broker_order)

            mapped["symbol"] = broker_order.get("symbol") or payload.get("symbol")

            mapped["side"] = broker_order.get("side") or payload.get("side")

            mapped["qty"] = broker_order.get("qty") or payload.get("qty")

            mapped["order_class"] = broker_order.get("order_class") or payload.get("order_class")

            mapped["raw_order"] = broker_order

            return mapped

        except httpx.RequestError as e:

            logger.error(

                "Failed to send Alpaca order payload.",

                extra={"error": str(e), "payload": payload},

            )

            return {

                "status": OrderStatus.FAILED,

                "reason": f"Request failed: {str(e)}",

                "payload": payload,

            }

    async def place_order(self, order: Order, update_callback: StatusUpdateCallable):

        response = await self._make_order_request(order)

        if not response or response.is_error:

            reason = f"Broker API request failed with status {response.status_code if response else 'N/A'}"

            if response:

                reason += f": {response.text}"

            await update_callback(

                {

                    "order_id": order.order_id,

                    "status": OrderStatus.FAILED,

                    "reason": reason,

                }

            )

            return

        broker_order = response.json()

        update_data = self._map_alpaca_order_to_internal(broker_order)

        update_data["order_id"] = order.order_id

        await update_callback(update_data)

    async def _make_order_request(self, order: Order) -> Optional[httpx.Response]:

        headers = self._get_auth_headers()

        headers["Content-Type"] = "application/json"

        reduce_only_exit = is_profit_lifecycle_exit(order)

        try:

            if reduce_only_exit:

                payload = build_alpaca_reduce_only_exit_payload(order)

            else:

                payload = build_alpaca_entry_payload(

                    order,

                    require_protection=True,

                    require_bracket=True,

                )

        except ProtectiveOrderError as exc:

            logger.error(

                "Refusing to submit invalid Alpaca order safety contract.",

                extra={

                    "order_id": order.order_id,

                    "symbol": order.symbol,

                    "error": str(exc),

                    "required_protection": (

                        "reduce_only_profit_exit"

                        if reduce_only_exit

                        else "bracket"

                    ),

                    "orders_changed": False,

                },

            )

            return httpx.Response(422, json={"message": str(exc)})

        logger.info(

            (

                "Submitting Alpaca reduce-only profit lifecycle exit "

                "through the close-position endpoint."

                if reduce_only_exit

                else "Submitting Alpaca bracket-protected entry order."

            ),

            extra={

                "order_id": order.order_id,

                "symbol": order.symbol,

                "order_class": (

                    "reduce_only_exit"

                    if reduce_only_exit

                    else payload.get("order_class")

                ),

                "has_stop_loss": bool(payload.get("stop_loss")),

                "has_take_profit": bool(payload.get("take_profit")),

            },

        )

        try:

            if reduce_only_exit:

                encoded_symbol = urllib.parse.quote(

                    str(payload["symbol_or_asset_id"]),

                    safe="",

                )

                return await self._client.delete(

                    self._url(f"/v2/positions/{encoded_symbol}"),

                    headers=headers,

                    params={"qty": payload["qty"]},

                )

            return await self._client.post(

                self._url("/v2/orders"),

                headers=headers,

                json=payload,

            )

        except httpx.RequestError as e:

            logger.error("Failed to send request to Alpaca.", extra={"error": str(e)})

            return None

    async def cancel_order(self, broker_order_id: str) -> dict:

        headers = self._get_auth_headers()

        try:

            response = await self._client.delete(

                self._url(f"/v2/orders/{broker_order_id}"),

                headers=headers,

            )

            if response.status_code == 204:

                return {

                    "status": OrderStatus.CANCELLED,

                    "broker_order_id": broker_order_id,

                }

            logger.error(

                "Failed to cancel Alpaca order.",

                extra={

                    "broker_order_id": broker_order_id,

                    "status_code": response.status_code,

                    "response": response.text,

                },

            )

            return {

                "status": "error",

                "message": f"Alpaca API error: {response.text}",

                "broker_order_id": broker_order_id,

            }

        except httpx.RequestError as e:

            logger.error(

                "Request error while cancelling Alpaca order.",

                extra={"error": str(e)},

            )

            return {

                "status": "error",

                "message": str(e),

                "broker_order_id": broker_order_id,

            }

    async def submit_exit_bracket_order(

        self,

        *,

        symbol: str,

        qty: Any,

        side: str,

        stop_price: Any,

        take_profit_price: Any,

        client_order_id: str | None = None,

    ) -> dict:

        """

        Submit an OCO exit order for an existing position.

        This is used by the stop-only upgrade flow:

        existing position + old stop-only order -> replacement OCO TP/SL order.

        """

        payload: Dict[str, Any] = {

            "symbol": symbol.upper(),

            "qty": str(qty),

            "side": side,

            "type": "limit",

            "time_in_force": "gtc",

            "order_class": "oco",

            "take_profit": {"limit_price": str(take_profit_price)},

            "stop_loss": {"stop_price": str(stop_price)},

        }

        if client_order_id:

            payload["client_order_id"] = client_order_id

        return await self._post_order_payload(payload)

    async def submit_protective_stop_order(

        self,

        *,

        symbol: str,

        qty: Any,

        side: str,

        stop_price: Any,

        client_order_id: str | None = None,

    ) -> dict:

        """

        Submit a protective stop order.

        This remains available only as rollback protection if an OCO/bracket upgrade fails.

        New entry orders should not use stop-only protection anymore.

        """

        payload: Dict[str, Any] = {

            "symbol": symbol.upper(),

            "qty": str(qty),

            "side": side,

            "type": "stop",

            "time_in_force": "gtc",

            "stop_price": str(stop_price),

        }

        if client_order_id:

            payload["client_order_id"] = client_order_id

        return await self._post_order_payload(payload)

    async def get_order_status(self, broker_order_id: str) -> dict:

        headers = self._get_auth_headers()

        try:

            response = await self._client.get(

                self._url(f"/v2/orders/{broker_order_id}"),

                headers=headers,

            )

            if response.is_error:

                return {

                    "status": "error",

                    "message": f"Alpaca API error: {response.text}",

                }

            broker_order = response.json()

            return self._map_alpaca_order_to_internal(broker_order)

        except httpx.RequestError as e:

            logger.error(

                "Request error while fetching Alpaca order status.",

                extra={"error": str(e)},

            )

            return {

                "status": "error",

                "message": str(e),

            }

    def _map_alpaca_order_to_internal(self, alpaca_order: dict) -> dict:

        status_map = {

            "new": OrderStatus.PLACED,

            "accepted": OrderStatus.PLACED,

            "pending_new": OrderStatus.PLACED,

            "accepted_for_bidding": OrderStatus.PLACED,

            "held": OrderStatus.PLACED,

            "stopped": OrderStatus.PLACED,

            "suspended": OrderStatus.PLACED,

            "calculated": OrderStatus.PLACED,

            "done_for_day": OrderStatus.PLACED,

            "pending_cancel": OrderStatus.PLACED,

            "pending_replace": OrderStatus.PLACED,

            "partially_filled": OrderStatus.PARTIALLY_FILLED,

            "filled": OrderStatus.EXECUTED,

            "canceled": OrderStatus.CANCELLED,

            "expired": OrderStatus.FAILED,

            "rejected": OrderStatus.FAILED,

        }

        alpaca_status = str(alpaca_order.get("status") or "").strip().lower()

        broker_order_id = alpaca_order.get("id")

        internal_status = status_map.get(alpaca_status)

        if internal_status is None:

            internal_status = OrderStatus.PLACED if broker_order_id else OrderStatus.FAILED

        update_data = {

            "status": internal_status,

            "broker_order_id": broker_order_id,

            "executed_quantity": int(float(alpaca_order.get("filled_qty", 0) or 0)),

            "broker_status": alpaca_status,

        }

        if alpaca_order.get("filled_avg_price"):

            update_data["avg_execution_price"] = float(alpaca_order["filled_avg_price"])

        if alpaca_order.get("filled_at"):

            update_data["executed_at"] = alpaca_order["filled_at"]

        if alpaca_status in {"rejected", "expired"}:

            update_data["reason"] = f"Alpaca {alpaca_status}: {alpaca_order.get('failed_reason', 'Unknown reason')}"

        return update_data

    async def execute(self, trade_order: TradeOrder) -> Dict[str, Any]:

        """

        Direct TradeOrder execution is intentionally blocked for Alpaca.

        Reason:

        TradeOrder does not carry guard_plan/protective_exit metadata, so it cannot

        create a broker-side bracket order with TP/SL. Use the /execute/trade-plan

        path instead, because TradePlanExecutionRequest contains risk + exit plan.

        """

        return {

            "status": OrderStatus.FAILED,

            "reason": (

                "Direct Alpaca execute() is disabled because it cannot attach broker-side TP/SL. "

                "Use /execute/trade-plan with guard_plan or protective_exit so the adapter can "

                "submit a bracket-protected order."

            ),

            "symbol": trade_order.symbol,

            "side": trade_order.side.value,

            "quantity": trade_order.quantity,

            "orders_changed": False,

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

    def _order_snapshot(self, item: Dict[str, Any]) -> Dict[str, Any]:

        return {

            "id": item.get("id"),

            "symbol": item.get("symbol"),

            "side": item.get("side"),

            "qty": item.get("qty"),

            "type": item.get("type"),

            "order_class": item.get("order_class"),

            "status": item.get("status"),

            "stop_price": item.get("stop_price"),

            "limit_price": item.get("limit_price"),

            "trail_price": item.get("trail_price"),

            "trail_percent": item.get("trail_percent"),

            "submitted_at": item.get("submitted_at"),

            "created_at": item.get("created_at"),

            "legs": item.get("legs"),

        }

    async def get_broker_order(self, broker_order_id: str) -> Dict[str, Any]:

        order = await self._get_json(f"/v2/orders/{broker_order_id}")

        return self._order_snapshot(order)

    async def get_open_orders(self) -> List[Dict[str, Any]]:

        orders = await self._get_json("/v2/orders?status=open&limit=100&nested=true")

        snapshots = [self._order_snapshot(item) for item in orders]

        hydrated: List[Dict[str, Any]] = []

        for item in snapshots:

            needs_detail = item.get("id") and not any(

                item.get(key) not in (None, "")

                for key in ("stop_price", "limit_price", "trail_price", "trail_percent")

            )

            if needs_detail:

                try:

                    detail = await self.get_broker_order(str(item["id"]))

                    item = {

                        **item,

                        **{

                            key: value

                            for key, value in detail.items()

                            if value not in (None, "")

                        },

                    }

                except Exception as exc:  # pragma: no cover - defensive broker detail fallback

                    logger.warning(

                        "Failed to fetch full Alpaca order details; using list-order snapshot.",

                        extra={

                            "broker_order_id": item.get("id"),

                            "error": str(exc),

                        },

                    )

            hydrated.append(item)

        return hydrated
