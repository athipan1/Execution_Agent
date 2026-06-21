# Execution Agent Workers

This service now supports two standalone worker entrypoints for production and paper-trading deployments.

## Execution Worker

Claims queued execution jobs from the configured Database Agent and processes them through the broker adapter.

```bash
PYTHONPATH=src python -m app.workers.execution_worker
```

Important behavior:

- Honors `TRADING_ENABLED`; when false, jobs remain queued and attempts are not consumed.
- Uses the same broker-mode guardrails as the API service.
- In `LIVE`, `ALLOW_LIVE_TRADING=true`, `BROKER_MODE=ALPACA`, and `DB_AGENT_URL` are required by config guardrails.

## Reconciliation Worker

Periodically refreshes in-flight broker orders and writes status changes back to the Database Agent.

```bash
PYTHONPATH=src python -m app.workers.reconciliation_worker
```

## Useful environment variables

```ini
EXECUTION_WORKER_POLL_SECONDS=2
RECONCILIATION_WORKER_POLL_SECONDS=30
RECONCILIATION_LIMIT=100
WORKER_RUN_ONCE=false
```

For smoke tests or CI-style one-shot runs:

```bash
WORKER_RUN_ONCE=true PYTHONPATH=src python -m app.workers.execution_worker
WORKER_RUN_ONCE=true PYTHONPATH=src python -m app.workers.reconciliation_worker
```

## Deployment pattern

Run API, execution worker, and reconciliation worker as separate processes/containers. Do not rely on a human or external scheduler to call `/jobs/process-next` in live trading.
