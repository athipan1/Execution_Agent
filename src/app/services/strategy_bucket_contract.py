from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from fastapi import HTTPException


VALID_STRATEGY_BUCKETS = {
    "core_dividend",
    "quality_growth",
    "value_rebound",
    "news_momentum",
    "unassigned",
}


def normalize_strategy_bucket(value: Any) -> str:
    bucket = str(value or "unassigned").strip().lower() or "unassigned"
    if bucket not in VALID_STRATEGY_BUCKETS:
        raise ValueError(f"unsupported strategy_bucket: {value!r}")
    return bucket


@dataclass(frozen=True)
class StrategyBucketDiagnostics:
    trade_id: str
    symbol: str
    context: str
    requested_bucket: str
    persisted_bucket: str

    @property
    def matched(self) -> bool:
        return self.requested_bucket == self.persisted_bucket

    def as_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "context": self.context,
            "requested_bucket": self.requested_bucket,
            "persisted_bucket": self.persisted_bucket,
            "matched": self.matched,
        }


class StrategyBucketPersistenceError(HTTPException):
    """Fail-closed contract error when Database_Agent changes/drops a bucket."""

    def __init__(self, diagnostics: StrategyBucketDiagnostics):
        self.diagnostics = diagnostics
        detail = (
            "strategy_bucket_persistence_mismatch: "
            f"trade_id={diagnostics.trade_id!r}, symbol={diagnostics.symbol!r}, "
            f"context={diagnostics.context!r}, requested={diagnostics.requested_bucket!r}, "
            f"persisted={diagnostics.persisted_bucket!r}"
        )
        super().__init__(status_code=409, detail=detail)


def strategy_bucket_diagnostics(order_request, order, *, context: str) -> StrategyBucketDiagnostics:
    return StrategyBucketDiagnostics(
        trade_id=str(getattr(order_request, "trade_id", "")),
        symbol=str(getattr(order_request, "symbol", getattr(order, "symbol", ""))).upper(),
        context=context,
        requested_bucket=normalize_strategy_bucket(getattr(order_request, "strategy_bucket", None)),
        persisted_bucket=normalize_strategy_bucket(getattr(order, "strategy_bucket", None)),
    )


def assert_strategy_bucket_persisted(order_request, order, *, context: str) -> StrategyBucketDiagnostics:
    diagnostics = strategy_bucket_diagnostics(order_request, order, context=context)
    if not diagnostics.matched:
        raise StrategyBucketPersistenceError(diagnostics)
    return diagnostics


def resolved_strategy_bucket_for_report(order_request, order) -> str:
    """Prefer a specific persisted bucket; never let `unassigned` hide request intent."""
    requested = normalize_strategy_bucket(getattr(order_request, "strategy_bucket", None))
    persisted = normalize_strategy_bucket(getattr(order, "strategy_bucket", None))
    if persisted != "unassigned":
        return persisted
    return requested
