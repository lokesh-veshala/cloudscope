"""Pure automatic-alert state transition rules."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal


def next_decision(*, existing_count: int, last_data_as_of: datetime | None,
                  event_exists: bool, data_as_of: datetime,
                  blocked_reason: str | None, cost: Decimal,
                  limit: Decimal, threshold: Decimal,
                  required_confirmations: int = 2) -> tuple[str, int]:
    """Return a fail-closed decision without performing I/O."""
    if limit <= 0 or cost < 0 or threshold <= 0 or required_confirmations < 1:
        return "BLOCKED", 0
    if blocked_reason:
        return "BLOCKED", 0
    if cost < limit * threshold / Decimal("100"):
        return "BELOW_THRESHOLD", 0
    if event_exists:
        return "ALREADY_TRIGGERED", max(existing_count, required_confirmations)
    if last_data_as_of is not None and data_as_of <= last_data_as_of:
        return "AWAITING_CONFIRMATION", existing_count
    confirmations = existing_count + 1
    return ("READY" if confirmations >= required_confirmations
            else "AWAITING_CONFIRMATION"), confirmations
