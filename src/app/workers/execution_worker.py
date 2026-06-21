import asyncio
from typing import Optional

from app.config import settings
from app.logging import get_logger
from app.models import ExecutionJob
from app.services.execution_service import ExecutionService
from app.workers.bootstrap import build_execution_service

logger = get_logger(__name__)


async def process_once(service: ExecutionService) -> Optional[ExecutionJob]:
    """Claim and process one queued execution job.

    The worker intentionally honors TRADING_ENABLED before claiming work. This
    keeps queued jobs in the database when trading is paused instead of marking
    them failed or consuming attempts during an operational halt.
    """
    if not settings.TRADING_ENABLED:
        logger.info("Execution worker skipped cycle because TRADING_ENABLED=false.")
        return None

    job = await service.process_next_execution_job()
    if job:
        logger.info(
            "Execution worker processed job.",
            extra={"job_id": job.job_id, "order_id": job.order_id, "status": job.status, "attempts": job.attempts},
        )
    return job


async def run_forever(service: Optional[ExecutionService] = None, *, poll_seconds: Optional[float] = None) -> None:
    service = service or build_execution_service()
    delay = float(poll_seconds if poll_seconds is not None else settings.EXECUTION_WORKER_POLL_SECONDS)
    delay = max(delay, 0.25)
    logger.info("Execution worker started.", extra={"poll_seconds": delay})

    while True:
        try:
            await process_once(service)
        except Exception as exc:
            logger.error("Execution worker cycle failed.", exc_info=True, extra={"error": str(exc)})
        if settings.WORKER_RUN_ONCE:
            break
        await asyncio.sleep(delay)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
