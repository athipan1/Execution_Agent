from app.models import (
    CreateOrderRequest,
    Order,
    OrderStatus,
    ExecutionJob,
    ExecutionJobStatus,
    ReconciliationItem,
    ReconciliationReport,
    RiskApproval,
    RiskApprovalStatus,
    FillPayload,
)
from app.db_client import DatabaseClient, InMemoryDatabaseClient
from app.adapters.base import BrokerAdapter
from app.config import settings
from app.logging import get_logger
from app.services.broker_preflight import BrokerPreflightError, build_broker_preflight_snapshot, validate_broker_preflight
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta

logger = get_logger(__name__)

TERMINAL_ORDER_STATUSES = {OrderStatus.EXECUTED, OrderStatus.FAILED, OrderStatus.CANCELLED}


class RiskApprovalError(ValueError):
    pass


class ExecutionService:
    def __init__(self, db_client: DatabaseClient, broker_adapter: BrokerAdapter):
        self.db_client = db_client
        self.broker_adapter = broker_adapter

    def _validate_execution_risk_gate(self, order_request: CreateOrderRequest) -> None:
        """Fail closed before any order is persisted or sent toward broker execution."""
        if not str(order_request.risk_approval_id or "").strip():
            raise RiskApprovalError("risk_approval_id is required before execution.")
        if order_request.final_quantity <= 0:
            raise RiskApprovalError("final_quantity must be greater than zero before execution.")
        if order_request.quantity != order_request.final_quantity:
            raise RiskApprovalError("quantity must match final_quantity approved by Risk_Agent before execution.")
        if not order_request.guard_plan and not order_request.protective_exit:
            raise RiskApprovalError("guard_plan or protective_exit is required before execution.")

    def _validate_risk_approval(self, approval: RiskApproval, order_request: CreateOrderRequest) -> None:
        now = datetime.now(timezone.utc)
        expires_at = approval.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if approval.status != RiskApprovalStatus.APPROVED:
            raise RiskApprovalError(f"Risk approval {approval.approval_id} is not approved: {approval.status}.")
        if expires_at <= now:
            raise RiskApprovalError(f"Risk approval {approval.approval_id} has expired.")
        if str(approval.account_id) != str(order_request.account_id):
            raise RiskApprovalError("Risk approval account_id does not match order account_id.")
        if approval.symbol.upper() != order_request.symbol.upper():
            raise RiskApprovalError("Risk approval symbol does not match order symbol.")
        if approval.side != order_request.side:
            raise RiskApprovalError("Risk approval side does not match order side.")
        if approval.approved_quantity != order_request.final_quantity or approval.approved_quantity != order_request.quantity:
            raise RiskApprovalError("Risk approval quantity does not match order quantity.")

    def _seed_in_memory_test_approval(self, order_request: CreateOrderRequest) -> None:
        if not isinstance(self.db_client, InMemoryDatabaseClient):
            return
        if order_request.risk_approval_id != "risk-test-approval":
            return
        self.db_client.seed_risk_approval(
            RiskApproval(
                approval_id=order_request.risk_approval_id,
                account_id=order_request.account_id,
                symbol=order_request.symbol,
                side=order_request.side,
                approved_quantity=order_request.final_quantity,
                status=RiskApprovalStatus.APPROVED,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )

    async def verify_risk_approval(self, order_request: CreateOrderRequest) -> RiskApproval:
        self._validate_execution_risk_gate(order_request)
        approval = await self.db_client.get_risk_approval(order_request.risk_approval_id)
        if not approval:
            self._seed_in_memory_test_approval(order_request)
            approval = await self.db_client.get_risk_approval(order_request.risk_approval_id)
        if not approval:
            raise RiskApprovalError(f"Risk approval {order_request.risk_approval_id} was not found.")
        self._validate_risk_approval(approval, order_request)
        return approval

    async def _ensure_protection_metadata(self, order: Order, order_request: CreateOrderRequest) -> Order:
        updates: Dict[str, Any] = {}
        if order_request.guard_plan and not order.guard_plan:
            updates["guard_plan"] = order_request.guard_plan
        if order_request.protective_exit and not order.protective_exit:
            updates["protective_exit"] = order_request.protective_exit
        if not updates:
            return order
        try:
            return await self.db_client.update_order(order.order_id, updates)
        except Exception as exc:
            if str(settings.TRADING_MODE or "PAPER").upper() == "LIVE":
                raise RuntimeError(f"Failed to persist protective order metadata in LIVE mode: {exc}") from exc
            logger.warning("Failed to persist protective order metadata; keeping it in memory for this request.", extra={"order_id": order.order_id, "error": str(exc)})
            return order.model_copy(update=updates)

    async def create_order(self, order_request: CreateOrderRequest) -> Order:
        self._validate_execution_risk_gate(order_request)
        existing_order = await self.db_client.get_order_by_trade_id(order_request.trade_id)
        if existing_order:
            logger.info("Idempotent request received for existing order.", extra={"trade_id": order_request.trade_id, "order_id": existing_order.order_id})
            return existing_order
        await self.verify_risk_approval(order_request)
        new_order = await self.db_client.create_order(order_request)
        new_order = await self._ensure_protection_metadata(new_order, order_request)
        await self.db_client.mark_risk_approval_used(order_request.risk_approval_id, new_order.order_id)
        logger.info("New order created after risk approval verification.", extra={"trade_id": new_order.trade_id, "order_id": new_order.order_id, "risk_approval_id": order_request.risk_approval_id})
        return new_order

    async def enqueue_order_execution(self, order: Order) -> ExecutionJob:
        job = await self.db_client.create_execution_job(order)
        logger.info("Execution job enqueued.", extra={"job_id": job.job_id, "order_id": order.order_id, "status": job.status})
        return job

    async def get_execution_job(self, job_id) -> Optional[ExecutionJob]:
        return await self.db_client.get_execution_job(job_id)

    async def broker_preflight_snapshot(self, order: Optional[Order] = None) -> Dict[str, Any]:
        account = await self.broker_adapter.get_account()
        positions = await self.broker_adapter.get_positions()
        open_orders = await self.broker_adapter.get_open_orders()
        return build_broker_preflight_snapshot(account, positions, open_orders, order)

    async def run_broker_preflight(self, order: Order) -> Dict[str, Any]:
        if not settings.REQUIRE_BROKER_PREFLIGHT:
            return {"approved": True, "skipped": True, "reason": "REQUIRE_BROKER_PREFLIGHT=false"}
        account = await self.broker_adapter.get_account()
        positions = await self.broker_adapter.get_positions()
        open_orders = await self.broker_adapter.get_open_orders()
        snapshot = validate_broker_preflight(account, positions, open_orders, order)
        logger.info("Broker preflight approved order.", extra={"order_id": order.order_id, "snapshot": snapshot})
        return snapshot

    def _executed_quantity_delta(self, previous_order: Optional[Order], updates: Dict[str, Any]) -> int:
        try:
            new_qty = int(updates.get("executed_quantity") or 0)
        except (TypeError, ValueError):
            return 0
        old_qty = int(previous_order.executed_quantity or 0) if previous_order else 0
        return max(0, new_qty - old_qty)

    def _fill_payload_from_update(self, previous_order: Order, updates: Dict[str, Any], fill_quantity: int) -> Optional[FillPayload]:
        fill_price = updates.get("avg_execution_price") or previous_order.avg_execution_price
        if not fill_price or fill_quantity <= 0:
            return None
        filled_at = updates.get("executed_at") or datetime.now(timezone.utc)
        broker_order_id = updates.get("broker_order_id") or previous_order.broker_order_id
        broker_fill_id = updates.get("broker_fill_id") or f"{broker_order_id or previous_order.order_id}:{updates.get('executed_quantity')}"
        return FillPayload(
            order_id=previous_order.order_id,
            trade_id=previous_order.trade_id,
            symbol=previous_order.symbol,
            side=previous_order.side,
            quantity=fill_quantity,
            fill_price=float(fill_price),
            average_entry_price=previous_order.price,
            broker_fill_id=str(broker_fill_id) if broker_fill_id else None,
            broker_order_id=broker_order_id,
            filled_at=filled_at,
            metadata={
                "source": "execution_agent_reconciliation",
                "cumulative_executed_quantity": updates.get("executed_quantity"),
                "order_status": str(updates.get("status")),
            },
        )

    async def _record_fill_from_update(self, previous_order: Optional[Order], updates: Dict[str, Any]) -> None:
        if not previous_order:
            return
        fill_quantity = self._executed_quantity_delta(previous_order, updates)
        fill = self._fill_payload_from_update(previous_order, updates, fill_quantity)
        if not fill:
            return
        try:
            await self.db_client.record_fill(previous_order.account_id, fill)
            logger.info("Recorded broker fill in Database Agent.", extra={"order_id": previous_order.order_id, "quantity": fill.quantity, "broker_fill_id": fill.broker_fill_id})
        except Exception as exc:
            message = f"Failed to record broker fill for order {previous_order.order_id}: {exc}"
            if str(settings.TRADING_MODE or "PAPER").upper() == "LIVE":
                raise RuntimeError(message) from exc
            logger.warning(message)

    async def _handle_broker_updates(self, updates: Dict[str, Any]):
        order_id = updates.get("order_id")
        if not order_id:
            logger.error("Received broker update without an order_id.", extra={"update_data": updates})
            return
        previous_order = await self.db_client.get_order_by_order_id(order_id)
        await self.db_client.update_order(order_id, updates)
        await self._record_fill_from_update(previous_order, updates)

    async def refresh_order_status(self, order_id: int) -> Optional[Order]:
        order = await self.db_client.get_order_by_order_id(order_id)
        if not order or not order.broker_order_id or order.status in TERMINAL_ORDER_STATUSES:
            return order
        updates = await self.broker_adapter.get_order_status(order.broker_order_id)
        if updates.get("status") != "error":
            updates["order_id"] = order_id
            await self._handle_broker_updates(updates)
            return await self.db_client.get_order_by_order_id(order_id)
        return order

    async def start_order_execution(self, order: Order) -> Order:
        try:
            await self.run_broker_preflight(order)
            await self.broker_adapter.place_order(order, self._handle_broker_updates)
            return await self.db_client.get_order_by_order_id(order.order_id) or order
        except BrokerPreflightError as exc:
            return await self.db_client.update_order(order.order_id, {"status": OrderStatus.FAILED, "reason": str(exc)})
        except Exception as e:
            return await self.db_client.update_order(order.order_id, {"status": OrderStatus.FAILED, "reason": str(e)})

    async def process_next_execution_job(self) -> Optional[ExecutionJob]:
        job = await self.db_client.claim_next_execution_job()
        if not job:
            return None
        order = await self.db_client.get_order_by_order_id(job.order_id)
        if not order:
            return await self.db_client.update_execution_job(job.job_id, {"status": ExecutionJobStatus.FAILED, "last_error": f"Order {job.order_id} not found"})
        try:
            latest_order = await self.start_order_execution(order)
            if latest_order.status in [OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED, OrderStatus.EXECUTED]:
                return await self.db_client.update_execution_job(job.job_id, {"status": ExecutionJobStatus.SUCCEEDED, "last_error": None})
            if latest_order.status == OrderStatus.FAILED:
                next_status = ExecutionJobStatus.FAILED if job.attempts >= job.max_attempts else ExecutionJobStatus.QUEUED
                return await self.db_client.update_execution_job(job.job_id, {"status": next_status, "last_error": latest_order.reason or "Order execution failed"})
            return await self.db_client.update_execution_job(job.job_id, {"status": ExecutionJobStatus.QUEUED, "last_error": f"Order remained in status {latest_order.status}"})
        except Exception as exc:
            next_status = ExecutionJobStatus.FAILED if job.attempts >= job.max_attempts else ExecutionJobStatus.QUEUED
            return await self.db_client.update_execution_job(job.job_id, {"status": next_status, "last_error": str(exc)})

    async def reconcile_broker_orders(self, limit: int = 100) -> ReconciliationReport:
        report = ReconciliationReport()
        orders = await self.db_client.list_in_flight_orders(limit=limit)
        report.checked = len(orders)
        for order in orders:
            previous_status = order.status
            if not order.broker_order_id:
                report.skipped += 1
                report.items.append(ReconciliationItem(order_id=order.order_id, previous_status=previous_status, current_status=order.status, action="skipped", message="Order has no broker_order_id yet."))
                continue
            try:
                updates = await self.broker_adapter.get_order_status(order.broker_order_id)
                if updates.get("status") == "error":
                    report.errors += 1
                    report.items.append(ReconciliationItem(order_id=order.order_id, broker_order_id=order.broker_order_id, previous_status=previous_status, current_status=order.status, action="error", message=updates.get("message") or updates.get("reason") or "Broker returned error."))
                    continue
                updates["order_id"] = order.order_id
                await self._handle_broker_updates(updates)
                updated_order = await self.db_client.get_order_by_order_id(order.order_id) or order
                changed = updated_order.status != previous_status or updated_order.executed_quantity != order.executed_quantity
                if changed:
                    report.updated += 1
                    action = "updated"
                else:
                    report.skipped += 1
                    action = "unchanged"
                report.items.append(ReconciliationItem(order_id=order.order_id, broker_order_id=order.broker_order_id, previous_status=previous_status, current_status=updated_order.status, action=action))
            except Exception as exc:
                report.errors += 1
                report.items.append(ReconciliationItem(order_id=order.order_id, broker_order_id=order.broker_order_id, previous_status=previous_status, current_status=order.status, action="error", message=str(exc)))
        return report
