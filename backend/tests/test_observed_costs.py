import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from observed_costs import estimate_observed_interval


class ObservedCostTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.price = {"status": "COMPLETE", "usd_per_hour": "0.60",
                      "fetched_at": self.start.isoformat(), "effective_at": self.start.isoformat()}
        metadata = {"instance_type": "test.large", "tags": {"Team": "Example"}, "pricing": self.price}
        self.previous = {"last_seen": self.start, "state": "running", "metadata": metadata}
        self.current = {"observed_at": self.start+timedelta(minutes=5), "state": "running",
                        "provider_resource_type": "ec2", "metadata": dict(metadata)}

    def test_five_minute_decimal_estimate(self):
        result = estimate_observed_interval(self.previous, self.current, self.price)
        self.assertAlmostEqual(result["amount"], Decimal("0.05"))
        self.assertEqual(result["basis"], "OBSERVED_EC2")

    def test_long_gap_is_not_backfilled(self):
        self.current["observed_at"] = self.start+timedelta(hours=4)
        self.assertIsNone(estimate_observed_interval(self.previous, self.current, self.price)["amount"])

    def test_tag_change_is_not_reassigned_retroactively(self):
        self.current["metadata"]["tags"] = {"Team": "NewOwner"}
        self.assertIsNone(estimate_observed_interval(self.previous, self.current, self.price)["amount"])

    def test_stopped_instance_does_not_zero_its_storage(self):
        self.current["state"] = "stopped"
        self.assertIsNone(estimate_observed_interval(self.previous, self.current, self.price)["amount"])

    def test_missing_price_and_rate_change_withhold_estimate(self):
        self.assertIsNone(estimate_observed_interval(self.previous, self.current, {})["amount"])
        self.assertIsNone(estimate_observed_interval(self.previous, self.current, {**self.price,"usd_per_hour":"0.70"})["amount"])

    def test_order_required(self):
        self.current["observed_at"] = self.start
        with self.assertRaises(ValueError):
            estimate_observed_interval(self.previous, self.current, self.price)

    def test_nonfinite_price_is_rejected(self):
        self.assertIsNone(estimate_observed_interval(self.previous, self.current, {**self.price,"usd_per_hour":"NaN"})["amount"])

    def test_stale_and_future_effective_prices_are_withheld(self):
        for field, timestamp in (("fetched_at", self.start-timedelta(days=2)),
                                 ("effective_at", self.start+timedelta(minutes=1))):
            old = dict(self.price)
            self.previous["metadata"]["pricing"] = {**old, field: timestamp.isoformat()}
            self.assertIsNone(estimate_observed_interval(self.previous, self.current, self.price)["amount"])

    def test_spot_assumption_is_separately_labeled(self):
        price = {**self.price, "status": "ESTIMATED", "usd_per_hour": "0.348"}
        self.previous["metadata"]["pricing"] = price
        result = estimate_observed_interval(self.previous, self.current, price)
        self.assertEqual(result["basis"], "ASSUMED_SPOT_DISCOUNT")
        self.assertAlmostEqual(result["amount"], Decimal("0.029"))
