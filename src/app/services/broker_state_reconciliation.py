from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

import httpx
from fastapi.encoders import jsonable_encoder

from app.adapters.base import BrokerAdapter
from app.config import settings
from app.services.broker_cleanup import classify_open_orders


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _account_flags(account: Dict[str, Any]) -> Dict[str, Any]:
    buying_power = _as_float(account.get("buying_power"))
    cash = _as_float(account.get("cash"))
    equity = _as_float(account.get("equity") or account.get("portfolio_value"))
    return {
        "buying_power_unavailable": buying_power <= 0,
        "cash_negative": cash < 0,
        "account_restricted": bool(account.get("trading_blocked") or account.get("account_blocked")),
        "equity": equity,
        "buying_power": buying_power,
        "cash": cash,
    }


class BrokerStateReconciliationService:
    def __init__(self, adapter: BrokerAdapter):
        self.adapter = adapter

    async def collect_broker_state(self, account_id: int | str = 1) -> Dict[str, Any]:
        account = await self.adapter.get_account()
        positions = await self.adapter.get_positions()
        open_orders = await self.adapter.get_open_orders()
        order_summary = classify_open_orders(open_orders, positions=positions)
        flags = _account_flags(account)
        return {
            "source": "execution_agent",
            "account_id": account_id,
            "broker": account.get("broker"),
            "paper": account.get("paper"),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "account": account,
            "positions": positions,
            "open_orders": open_orders,
            "summary": {
                "position_count": len(positions or []),
                "open_order_count": len(open_orders or []),
                "stale_order_count": order_summary.get("stale_order_count", 0),
                "fresh_order_count": order_summary.get("fresh_order_count", 0),
                "unknown_age_order_count": order_summary.get("unknown_age_order_count", 0),
                "protective_order_count": order_summary.get("protective_order_count", 0),
                "stale_entry_order_count": order_summary.get("stale_entry_order_count", 0),
                "stale_non_entry_order_count": order_summary.get("stale_non_entry_order_count", 0),
                **flags,
            },
            "order_classification": order_summary,
        }

    async def push_broker_state_to_database(self, broker_state: Dict[str, Any]) -> Dict[str, Any]:
        if not settings.DB_AGENT_URL:
            return {
                "status": "skipped",
                "reason": "DB_AGENT_URL not configured",
                "endpoint": None,
            }
        endpoint = settings.BROKER_SYNC_ENDPOINT if settings.BROKER_SYNC_ENDPOINT.startswith("/") else f"/{settings.BROKER_SYNC_ENDPOINT}"
        url = f"{settings.DB_AGENT_URL.rstrip('/')}{endpoint}"
        headers = {"X-API-KEY": settings.DATABASE_AGENT_API_KEY or settings.API_KEY}
        async with httpx.AsyncClient(timeout=float(settings.BROKER_SYNC_TIMEOUT_SECONDS)) as client:
            try:
                response = await client.post(url, json=jsonable_encoder(broker_state), headers=headers)
                payload: Any
                try:
                    payload = response.json()
                except Exception:
                    payload = response.text
                if response.is_error:
                    return {
                        "status": "failed",
                        "endpoint": endpoint,
                        "http_status": response.status_code,
                        "response": payload,
                    }
                return {
                    "status": "success",
                    "endpoint": endpoint,
                    "http_status": response.status_code,
                    "response": payload,
                }
            except Exception as exc:
                return {
                    "status": "failed",
                    "endpoint": endpoint,
                    "error": str(exc),
                }

    async def reconcile(self, account_id: int | str = 1, *, push_to_database: bool = True) -> Dict[str, Any]:
        broker_state = await self.collect_broker_state(account_id=account_id)
        database_sync = await self.push_broker_state_to_database(broker_state) if push_to_database else {"status": "skipped", "reason": "push_to_database=false"}
        ok = database_sync.get("status") in {"success", "skipped"}
        if settings.FAIL_RECONCILE_WHEN_DB_SYNC_FAILS and database_sync.get("status") == "failed":
            ok = False
        return {
            "ok": ok,
            "broker_state": broker_state,
            "database_sync": database_sync,
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
        }
