"""Validate a fresh broker clock immediately before an Alpaca order mutation."""
from datetime import datetime, timezone
import re


def _aware(value):
    if not isinstance(value, str) or not re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)', value
    ):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def validate_execution_clock(clock, now=None):
    clock = clock if isinstance(clock, dict) else {}
    now = now or datetime.now(timezone.utc)
    timestamp = _aware(clock.get("timestamp"))
    next_open, next_close = _aware(clock.get("next_open")), _aware(clock.get("next_close"))
    is_open = clock.get("is_open")
    age = (now - timestamp).total_seconds() if timestamp else None
    ordered = (next_close < next_open if is_open is True else next_open < next_close) if next_open and next_close else False
    boundary = next_close if is_open is True else next_open
    if (type(is_open) is not bool or age is None or not -2 <= age <= 30
            or not ordered or boundary <= now):
        return "session_unverified"
    return "open" if is_open else "market_closed"
