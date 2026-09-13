import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cost_engine.models import PriceInterval, UsageInterval
from cost_engine.pricing import CostEngine, PricingCoverageError

UTC = timezone.utc
T0 = datetime(2026, 9, 13, 8, tzinfo=UTC)


class CostEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = CostEngine()

    def test_on_demand_uses_exact_seconds(self):
        usage = UsageInterval("i-1", T0, T0 + timedelta(hours=2, minutes=35))
        price = PriceInterval(T0, T0 + timedelta(days=1), Decimal("0.272"), "sku-a")
        result = self.engine.calculate(usage, [price], require_complete=True)
        self.assertEqual(
            result.amount_usd,
            Decimal("0.70266666666666666666666666666666666666"),
        )
        self.assertEqual(self.engine.display_amount(result.amount_usd), Decimal("0.70"))
        self.assertTrue(result.is_complete)

    def test_spot_price_is_segmented_at_every_change(self):
        usage = UsageInterval("i-spot", T0, T0 + timedelta(hours=5))
        prices = [
            PriceInterval(T0 - timedelta(hours=1), T0 + timedelta(hours=1), Decimal(".20"), "p1"),
            PriceInterval(T0 + timedelta(hours=1), T0 + timedelta(hours=4), Decimal(".24"), "p2"),
            PriceInterval(T0 + timedelta(hours=4), T0 + timedelta(hours=8), Decimal(".18"), "p3"),
        ]
        self.assertEqual(
            self.engine.calculate(usage, prices, require_complete=True).amount_usd,
            Decimal("1.10"),
        )

    def test_missing_price_does_not_become_zero(self):
        usage = UsageInterval("i-gap", T0, T0 + timedelta(hours=3))
        prices = [PriceInterval(T0, T0 + timedelta(hours=1), Decimal(".2"), "p1")]
        result = self.engine.calculate(usage, prices)
        self.assertEqual(result.coverage, Decimal("0.3333333333333333333333333333"))
        self.assertEqual(len(result.unresolved_intervals), 1)
        with self.assertRaises(PricingCoverageError):
            self.engine.calculate(usage, prices, require_complete=True)

    def test_overlapping_prices_are_rejected(self):
        usage = UsageInterval("i-overlap", T0, T0 + timedelta(hours=2))
        prices = [
            PriceInterval(T0, T0 + timedelta(hours=2), Decimal(".2"), "p1"),
            PriceInterval(T0 + timedelta(hours=1), T0 + timedelta(hours=3), Decimal(".3"), "p2"),
        ]
        with self.assertRaises(ValueError):
            self.engine.calculate(usage, prices)

    def test_naive_timestamps_are_rejected(self):
        with self.assertRaises(ValueError):
            UsageInterval("bad", datetime(2026, 1, 1), T0)


if __name__ == "__main__":
    unittest.main()
