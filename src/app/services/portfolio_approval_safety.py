from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List

from app.models import PortfolioRiskApproval

VALID_STRATEGY_BUCKETS = {"core_dividend", "value_rebound", "news_momentum", "unassigned"}


def _quantity(approval: PortfolioRiskApproval) -> int:
    data = approval.risk_response or approval.risk or {}
    raw = approval.final_quantity or data.get("final_quantity") or data.get("approved_quantity") or approval.approved_quantity or 0
    try:
        return int(float(raw or 0))
    except (TypeError, ValueError):
        return 0


def _approval_id(approval: PortfolioRiskApproval) -> str:
    data = approval.risk_response or approval.risk or {}
    return str(approval.risk_approval_id or data.get("risk_approval_id") or data.get("approval_id") or "").strip()


def _has_guard(approval: PortfolioRiskApproval) -> bool:
    data = approval.risk_response or approval.risk or {}
    guard = data.get("guard_plan") or data.get("protective_exit")
    return isinstance(guard, dict) and bool(guard)


def validate_portfolio_approvals(
    approvals: Iterable[PortfolioRiskApproval],
    *,
    max_positions: int = 5,
    max_news_momentum_positions: int = 1,
) -> Dict[str, Any]:
    rows = list(approvals)
    errors: List[Dict[str, Any]] = []
    symbols = [str(row.symbol).upper() for row in rows]
    symbol_counts = Counter(symbols)
    bucket_counts = Counter(str(row.strategy_bucket or "unassigned") for row in rows if row.approved)

    if len(rows) > max_positions:
        errors.append({"code": "MAX_PORTFOLIO_APPROVALS_EXCEEDED", "count": len(rows), "max": max_positions})

    duplicate_symbols = sorted(symbol for symbol, count in symbol_counts.items() if count > 1)
    if duplicate_symbols:
        errors.append({"code": "DUPLICATE_SYMBOL_IN_PORTFOLIO_APPROVALS", "symbols": duplicate_symbols})

    if bucket_counts.get("news_momentum", 0) > max_news_momentum_positions:
        errors.append({"code": "NEWS_MOMENTUM_APPROVAL_LIMIT_EXCEEDED", "count": bucket_counts.get("news_momentum", 0), "max": max_news_momentum_positions})

    invalid_buckets = sorted({str(row.strategy_bucket) for row in rows if str(row.strategy_bucket) not in VALID_STRATEGY_BUCKETS})
    if invalid_buckets:
        errors.append({"code": "INVALID_STRATEGY_BUCKET", "buckets": invalid_buckets})

    missing_fields: List[Dict[str, Any]] = []
    for row in rows:
        if not row.approved:
            continue
        missing = []
        if _quantity(row) <= 0:
            missing.append("final_quantity")
        if not _approval_id(row):
            missing.append("risk_approval_id")
        if not _has_guard(row):
            missing.append("guard_plan")
        if missing:
            missing_fields.append({"symbol": str(row.symbol).upper(), "missing": missing})
    if missing_fields:
        errors.append({"code": "APPROVED_POSITION_MISSING_REQUIRED_FIELDS", "items": missing_fields})

    return {
        "approved": not errors,
        "errors": errors,
        "summary": {
            "approval_count": len(rows),
            "approved_count": sum(1 for row in rows if row.approved),
            "bucket_counts": dict(bucket_counts),
            "symbols": symbols,
        },
    }
