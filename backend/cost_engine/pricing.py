from __future__ import annotations

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Iterable

from .models import CostResult, PriceInterval, UsageInterval


class PricingCoverageError(RuntimeError):
    """Raised when a caller explicitly requires a fully-priced result."""


class CostEngine:
    """Prices half-open UTC usage intervals with exact Decimal arithmetic."""

    SECONDS_PER_HOUR = Decimal("3600")

    def calculate(
        self,
        usage: UsageInterval,
        prices: Iterable[PriceInterval],
        *,
        require_complete: bool = False,
    ) -> CostResult:
        ordered = sorted(prices, key=lambda p: (p.start, p.end, p.source_id))
        self._assert_non_overlapping(ordered)
        cursor = usage.start
        amount = Decimal("0")
        covered = Decimal("0")
        unresolved: list[tuple[datetime, datetime]] = []

        with localcontext() as context:
            context.prec = 38
            for price in ordered:
                start = max(usage.start, price.start)
                end = min(usage.end, price.end)
                if end <= start:
                    continue
                if start > cursor:
                    unresolved.append((cursor, start))
                if start < cursor:
                    start = cursor
                if end <= start:
                    continue
                seconds = Decimal(str((end - start).total_seconds()))
                amount += (
                    seconds / self.SECONDS_PER_HOUR
                    * usage.quantity_per_hour
                    * price.usd_per_unit_hour
                )
                covered += seconds
                cursor = end

            if cursor < usage.end:
                unresolved.append((cursor, usage.end))

        requested = Decimal(str((usage.end - usage.start).total_seconds()))
        result = CostResult(amount, covered, requested, tuple(unresolved))
        if require_complete and not result.is_complete:
            raise PricingCoverageError(
                f"pricing coverage {result.coverage:.6%}; "
                f"{len(result.unresolved_intervals)} unresolved interval(s)"
            )
        return result

    @staticmethod
    def display_amount(amount: Decimal) -> Decimal:
        """Round only at the presentation boundary using financial half-even."""
        return amount.quantize(Decimal("0.01"))

    @staticmethod
    def _assert_non_overlapping(prices: list[PriceInterval]) -> None:
        prior: PriceInterval | None = None
        for current in prices:
            if prior and current.start < prior.end:
                raise ValueError(
                    f"overlapping price intervals: {prior.source_id} and {current.source_id}"
                )
            prior = current

