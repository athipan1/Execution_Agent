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
)
from app.db_client import DatabaseClient
from app.adapters.base import BrokerAdapter
from app.logging import get_logger
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = get_logger(__name__)

TERMINAL_ORDER_STATUSES = {OrderStatus.EXECUTED, OrderStatus.FAILED, OrderStatus.CANCELLED}


class RiskApprovalError(ValueError):
    pass


class ExecutionService:
    """
    Orchestrates the order lifecycle, coordinating between the database,
    durable execution jobs, and the broker.
    """
    def __init__(self, db_client: DatabaseClient, broker_adapter: BrokerAdapter):
        self.db_client = db_client
        self.broker_adapter = broker_adapter

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

    async def verify_risk_approval(self, order_request: CreateOrderRequest) -> RiskApproval:
        approval = await self.db_client.get_risk_approval(order_request.risk_approval_id)
        if not approval:
            raise RiskApprovalError(f"Risk approval {order_request.risk_approval_id} was not found.")
        self._validate_risk_approval(approval, order_request)
        return approval

    async def create_order(self, order_request: CreateOrderRequest) -> Order:
        existing_order = await self.db_client.get_order_by_trade_id(order_request.trade_id)
        if existing_order:
            logger.info("Idempotent request received for existing order.", extra={"trade_id": order_request.trade_id, "order_id": existing_order.order_id})
            return existing_order

        await self.verify_risk_approval(order_request)
        new_order = await self.db_client.create_order(order_request)
        await self.db_client.mark_risk_approval_used(order_request.risk_approval_id, new_order.order_id)
        logger.info("New order created after risk approval verification.", extra={"trade_id": new_order.trade_id, "order_id": new_order.order_id, "risk_approval_id": order_request.risk_approval_id})
        return new_order

    async def enqueue_order_execution(self, order: Order) -> ExecutionJob:
        job = await self.db_client.create_execution_job(order)
        logger.info("Execution job enqueued.", extra={"job_id": job.job_id, "order_id": job.order_id, "status": job.status})
        return job

    async def get_execution_job(self, job_id) -> Optional[ExecutionJob]:
        return await self.db_client.get_execution_job(job_id)

    async def _handle_broker_updates(self, updates: Dict[str, Any]):
        order_id = updates.get("order_id")
        if not order_id:
            logger.error("Received broker update without an order_id.", extra={"update_data": updates})
            return
        logger.info("Received broker update for order.", extra={"order_id": order_id, "status": updates.get("status")})
        await self.db_client.update_order(order_id, updates)

    async def refresh_order_status(self, order_id: int) -> Optional[Order]:
        order = await self.db_client.get_order_by_order_id(order_id)
        if not order:
            return None
        if not order.broker_order_id:
            return order
        if order.status in TERMINAL_ORDER_STATUSES:
            return order
        updates = await self.broker_adapter.get_order_status(order.broker_order_id)
        if updates.get("status") != "error":
            updates["order_id"] = order_id
            await self._handle_broker_updates(updates)
            return await self.db_client.get_order_by_order_id(order_id)
        return order

    async def start_order_execution(self, order: Order) -> Order:
        logger.info("Starting execution for order.", extra={"order_id": order.order_id, "symbol": order.symbol})
        try:
            await self.broker_adapter.place_order(order, self._handle_broker_updates)
            return await self.db_client.get_order_by_order_id(order.order_id) or order
        except Exception as e:
            logger.error("Order execution failed.", extra={"order_id": order.order_id, "error": str(e)}, exc_info=True)
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
                updated_order = await self.db_client.update_order(order.order_id, updates)
                changed = updated_order.status != previous_status or updated_order.executed_quantity != order.executed_quantity
                if changed:
                    report.updated += 1
                    action = "updated"
                else:
                    report.skipped += 1
                    action = "unchanged"
                report.items.append(ReconciliationItem(order_id=order.order_id, broker_order_id=order.broker_order_id, previous_status=previous_status, current_status=updated_order.status, action=action))
            except Exception as exc:
                logger.error("Broker reconciliation failed for order.", extra={"order_id": order.order_id, "broker_order_id": order.broker_order_id, "error": str(exc)}, exc_info=True)
                report.errors += 1
                report.items.append(ReconciliationItem(order_id=order.order_id, broker_order_id=order.broker_order_id, previous_status=previous_status, current_status=order.status, action="error", message=str(exc)))

        return report
