from unittest.mock import AsyncMock, patch

import pytest

from app.models import ExecutionJob, ExecutionJobStatus, ReconciliationReport
from app.workers.execution_worker import process_once
from app.workers.reconciliation_worker import reconcile_once


@pytest.mark.asyncio
async def test_execution_worker_skips_when_trading_disabled():
    service = AsyncMock()

    with patch("app.workers.execution_worker.settings.TRADING_ENABLED", False):
        result = await process_once(service)

    assert result is None
    service.process_next_execution_job.assert_not_called()


@pytest.mark.asyncio
async def test_execution_worker_processes_one_job_when_enabled():
    job = ExecutionJob(job_id=1, order_id=42, trade_id="trade-42", status=ExecutionJobStatus.SUCCEEDED, attempts=1)
    service = AsyncMock()
    service.process_next_execution_job.return_value = job

    with patch("app.workers.execution_worker.settings.TRADING_ENABLED", True):
        result = await process_once(service)

    service.process_next_execution_job.assert_awaited_once()
    assert result == job


@pytest.mark.asyncio
async def test_reconciliation_worker_runs_once_with_limit():
    report = ReconciliationReport(checked=2, updated=1, skipped=1, errors=0)
    service = AsyncMock()
    service.reconcile_broker_orders.return_value = report

    result = await reconcile_once(service, limit=25)

    service.reconcile_broker_orders.assert_awaited_once_with(limit=25)
    assert result == report
