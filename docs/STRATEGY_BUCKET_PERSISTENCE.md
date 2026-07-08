# Strategy Bucket Persistence Contract

Execution_Agent treats `strategy_bucket` as an execution-safety invariant.

For every order request:

```text
requested strategy_bucket == persisted Database_Agent strategy_bucket
```

The invariant is checked in two paths:

1. New order creation (`create_response` and post-metadata update response)
2. Idempotent replay (`idempotent_lookup`)

A mismatch returns HTTP 409 through `StrategyBucketPersistenceError`. Newly created mismatched orders are marked `failed` and the Risk approval is not consumed. Idempotent existing orders are not mutated, but replay is rejected.

Supported values:

- `core_dividend`
- `value_rebound`
- `news_momentum`
- `unassigned`

`unassigned` is never allowed to silently hide a specific bucket requested by the caller.
