# Execution_Agent API Contract

This document defines the baseline API contract for `Execution_Agent` in the multi-agent trading system.

`Execution_Agent` is the final execution boundary. It creates orders, queues execution jobs, talks to the configured broker adapter, reconciles broker state, and must remain protected by strong runtime guardrails.

## Standard Headers

Every protected internal request should include:

```http
Content-Type: application/json
X-Correlation-ID: <uuid>
X-API-KEY: <execution-agent-api-key>
```

Operational endpoints such as `/health`, `/ready`, and `/version` may be public if they do not expose secrets or perform write actions.

## Standard Response Envelope

Every response should use this envelope:

```json
{
  "status": "success",
  "agent_type": "execution-agent",
  "version": "1.0.0",
  "schema_version": "1.0",
  "timestamp": "2026-07-04T00:00:00Z",
  "correlation_id": "00000000-0000-0000-0000-000000000000",
  "data": {},
  "metadata": {},
  "error": null,
  "confidence_score": null
}
```

## Required Operational Endpoints

```http
GET /health
GET /ready
GET /version
```

| Endpoint | Purpose |
| --- | --- |
| `/health` | Reports process and broker-health status. |
| `/ready` | Reports whether runtime configuration is safe enough for execution workflows. |
| `/version` | Reports agent response version, service version, schema version, and contract metadata. |

## Runtime Readiness Rules

`/ready` should report:

- `trading_mode`
- `trading_enabled`
- `allow_live_trading`
- `broker_mode`
- `broker_mode_supported`
- `live_guard_ok`
- `require_broker_preflight`
- `fail_on_stale_open_orders`
- `db_mode`
- `db_agent_url_configured`

## Safety Rules

1. `Execution_Agent` must never execute live orders unless `TRADING_MODE=LIVE`, `ALLOW_LIVE_TRADING=true`, and `BROKER_MODE=ALPACA` are all intentionally configured.
2. Simulator fallback is forbidden in live mode.
3. Execution endpoints must remain protected by API key middleware.
4. Order requests must require Risk approval metadata before execution.
5. Broker preflight and reconciliation should remain separate from contract/readiness checks.
6. Manager remains the orchestrator; Execution only executes approved orders.
