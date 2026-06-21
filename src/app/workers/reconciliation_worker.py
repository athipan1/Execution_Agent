import asyncio
from typing import Optional

from app.config import settings
from app.logging import get_logger
from app.models import ReconciliationReport
from app.services.execution_service import ExecutionService
from app.workers.bootstrap import build_execution_service

logger = get_logger(__name__)


async def reconcile_once(service: ExecutionService, *, limit: Optional[int] = None) -> ReconciliationReport:
    """Refresh broker status for in-flight orders once."""
    effective_limit = int(limit if limit is not None else settings.RECONCILIATION_LIMIT)
    report = await service.reconcile_broker_orders(limit=effective_limit)
    logger.info(
        "Reconciliation worker completed cycle.",
        extra={
            "checked": report.checked,
            "updated": report.updated,
            "skipped": report.skipped,
            "errors": report.errors,
        },
    )
    return report


async def run_forever(service: Optional[ExecutionService] = None, *, poll_seconds: Optional[float] = None) -> None:
    service = service or build_execution_service()
    delay = float(poll_seconds if poll_seconds is not None else settings.RECONCILIATION_WORKER_POLL_SECONDS)
    delay = max(delay, 1.0)
    logger.info("Reconciliation worker started.", extra={"poll_seconds": delay, "limit": settings.RECONCILIATION_LIMIT})

    while True:
        try:
            await reconcile_once(service)
        except Exception as exc:
            logger.error("Reconciliation worker cycle failed.", exc_info=True, extra={"error": str(exc)})
        if settings.WORKER_RUN_ONCE:
            break
        await asyncio.sleep(delay)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
