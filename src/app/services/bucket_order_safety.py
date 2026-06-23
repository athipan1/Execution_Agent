from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Set

from app.models import CreateOrderRequest

DEFAULT_MAX_ORDERS_PER_RUN = 5
DEFAULT_MAX_NEWS_MOMENTUM_ORDERS = 1
VALID_STRATEGY_BUCKETS = {"core_dividend", "value_rebound", "news_momentum", "unassigned"}


def _bucket(order: CreateOrderRequest) -> str:
    value = getattr(order, "strategy_bucket", None) or "unassigned"
    return value if value in VALID_STRATEGY_BUCKETS else "unassigned"


def validate_bucket_order_batch(
    orders: List[CreateOrderRequest],
    *,
    existing_open_symbols: Iterable[str] | None = None,
    max_orders_per_run: int = DEFAULT_MAX_ORDERS_PER_RUN,
    max_news_momentum_orders: int = DEFAULT_MAX_NEWS_MOMENTUM_ORDERS,
) -> Dict[str, Any]:
    """Validate a controlled batch of order requests before any order is created.

    This helper is intentionally side-effect free. It does not create, enqueue,
    or submit orders. It returns a structured approval/rejection report.
    """
    existing_symbols: Set[str] = {str(symbol).upper() for symbol in (existing_open_symbols or []) if symbol}
    symbols = [str(order.symbol).upper() for order in orders]
    counts = Counter(symbols)
    bucket_counts = Counter(_bucket(order) for order in orders)
    errors: List[Dict[str, Any]] = []

    if len(orders) > max_orders_per_run:
        errors.append({
            "code": "MAX_ORDERS_PER_RUN_EXCEEDED",
            "message": f"Batch has {len(orders)} orders; max allowed is {max_orders_per_run}.",
        })

    duplicate_symbols = sorted(symbol for symbol, count in counts.items() if count > 1)
    if duplicate_symbols:
        errors.append({
            "code": "DUPLICATE_SYMBOL_IN_BATCH",
            "symbols": duplicate_symbols,
            "message": "Batch contains duplicate symbols.",
        })

    overlap_symbols = sorted(symbol for symbol in set(symbols) if symbol in existing_symbols)
    if overlap_symbols:
        errors.append({
            "code": "SYMBOL_ALREADY_HAS_OPEN_ORDER",
            "symbols": overlap_symbols,
            "message": "One or more symbols already have open orders.",
        })

    if bucket_counts.get("news_momentum", 0) > max_news_momentum_orders:
        errors.append({
            "code": "NEWS_MOMENTUM_LIMIT_EXCEEDED",
            "message": f"news_momentum orders {bucket_counts.get('news_momentum', 0)} exceed limit {max_news_momentum_orders}.",
        })

    invalid_buckets = sorted({str(getattr(order, "strategy_bucket", "unassigned")) for order in orders if _bucket(order) == "unassigned" and getattr(order, "strategy_bucket", "unassigned") not in {None, "unassigned"}})
    if invalid_buckets:
        errors.append({
            "code": "INVALID_STRATEGY_BUCKET",
            "buckets": invalid_buckets,
            "message": "One or more orders have invalid strategy_bucket values.",
        })

    return {
        "approved": not errors,
        "errors": errors,
        "summary": {
            "order_count": len(orders),
            "max_orders_per_run": max_orders_per_run,
            "bucket_counts": dict(bucket_counts),
            "symbols": symbols,
        },
    }
