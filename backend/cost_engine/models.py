from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Mapping


def require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True)
class UsageInterval:
    resource_id: str
    start: datetime
    end: datetime
    quantity_per_hour: Decimal = Decimal("1")
    dimensions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_utc(self.start, "start")
        require_utc(self.end, "end")
        if self.end <= self.start:
            raise ValueError("usage interval must be non-empty")
        if self.quantity_per_hour < 0:
            raise ValueError("quantity_per_hour must be non-negative")


@dataclass(frozen=True)
class PriceInterval:
    start: datetime
    end: datetime
    usd_per_unit_hour: Decimal
    source_id: str

    def __post_init__(self) -> None:
        require_utc(self.start, "start")
        require_utc(self.end, "end")
        if self.end <= self.start:
            raise ValueError("price interval must be non-empty")
        if self.usd_per_unit_hour < 0:
            raise ValueError("price must be non-negative")
        if not self.source_id:
            raise ValueError("price source_id is required")


@dataclass(frozen=True)
class CostResult:
    amount_usd: Decimal
    covered_seconds: Decimal
    requested_seconds: Decimal
    unresolved_intervals: tuple[tuple[datetime, datetime], ...] = ()

    @property
    def coverage(self) -> Decimal:
        if self.requested_seconds == 0:
            return Decimal("1")
        return self.covered_seconds / self.requested_seconds

    @property
    def is_complete(self) -> bool:
        return self.covered_seconds == self.requested_seconds and not self.unresolved_intervals

