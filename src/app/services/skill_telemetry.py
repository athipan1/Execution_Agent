from __future__ import annotations

from typing import Any, Dict, Optional

from app.models import FillPayload, Order


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nested_get(payload: Dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def extract_skill_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize Curator/skill metadata carried by Manager into an execution shape."""
    metadata = _as_dict(metadata)
    curator_signal = _as_dict(metadata.get("curator_signal"))
    execution = _as_dict(curator_signal.get("execution"))
    selected_skill = _as_dict(curator_signal.get("selected_skill"))
    output = _as_dict(execution.get("output"))
    database_telemetry = _as_dict(execution.get("database_telemetry"))

    skill_id = (
        metadata.get("skill_id")
        or metadata.get("curator_skill_id")
        or curator_signal.get("skill_id")
        or selected_skill.get("skill_id")
    )
    execution_log_id = (
        metadata.get("skill_execution_log_id")
        or metadata.get("curator_skill_execution_log_id")
        or curator_signal.get("skill_execution_log_id")
        or database_telemetry.get("execution_log_id")
    )
    confidence = (
        metadata.get("skill_confidence")
        or curator_signal.get("confidence")
        or selected_skill.get("score")
        or output.get("confidence")
    )
    signal = metadata.get("skill_signal") or curator_signal.get("signal") or output.get("signal")

    return {
        "skill_id": str(skill_id) if skill_id else None,
        "execution_log_id": str(execution_log_id) if execution_log_id else None,
        "signal": signal,
        "confidence": confidence,
        "raw_curator_signal": curator_signal,
    }


def build_skill_trade_outcome_payload(
    *,
    order: Order,
    fill: FillPayload,
    realized_pl: Optional[float],
) -> Optional[Dict[str, Any]]:
    """Build Database_Agent /skills/trade-outcomes payload from an executed fill.

    The payload is emitted only when Manager/Curator metadata can link the fill
    back to a specific Curator skill execution log. Missing metadata is treated
    as a safe no-op.
    """
    skill_meta = extract_skill_metadata(getattr(order, "metadata", None))
    if not skill_meta.get("execution_log_id") or not skill_meta.get("skill_id"):
        return None

    outcome = None
    if realized_pl is not None:
        if realized_pl > 0:
            outcome = "win"
        elif realized_pl < 0:
            outcome = "loss"
        else:
            outcome = "breakeven"

    return {
        "execution_log_id": skill_meta["execution_log_id"],
        "skill_id": skill_meta["skill_id"],
        "account_id": str(order.account_id),
        "symbol": order.symbol.upper(),
        "strategy_bucket": order.strategy_bucket,
        "entry_price": order.price,
        "exit_price": fill.fill_price,
        "realized_pl": realized_pl,
        "outcome": outcome,
        "source_agent": "execution-agent",
        "metadata": {
            "order_id": order.order_id,
            "trade_id": order.trade_id,
            "broker_order_id": fill.broker_order_id,
            "broker_fill_id": fill.broker_fill_id,
            "fill_quantity": fill.quantity,
            "skill_signal": skill_meta.get("signal"),
            "skill_confidence": skill_meta.get("confidence"),
        },
        "closed_at": fill.filled_at,
    }
