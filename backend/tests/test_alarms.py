import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cost_engine.alarms import AlarmDecision, AlarmEvaluator, AlarmPolicy, MemoryAlarmState

UTC = timezone.utc
NOW = datetime(2026, 9, 13, 17, 22, tzinfo=UTC)


def policy(version=1, threshold="80"):
    return AlarmPolicy(
        dashboard_id="hpc",
        limit_version=version,
        limit_amount=Decimal("5000"),
        threshold_percent=Decimal(threshold),
        period_start=datetime(2026, 9, 1, tzinfo=UTC),
        period_end=datetime(2026, 10, 1, tzinfo=UTC),
    )


class AlarmTests(unittest.TestCase):
    def setUp(self):
        self.state = MemoryAlarmState()
        self.evaluator = AlarmEvaluator(self.state)

    def evaluate(self, p, cost="4025", coverage="1", age_seconds=30):
        return self.evaluator.evaluate(
            p,
            estimated_cost=Decimal(cost),
            pricing_coverage=Decimal(coverage),
            data_as_of=NOW - timedelta(seconds=age_seconds),
            evaluated_at=NOW,
        )

    def test_threshold_requires_two_consecutive_confirmations(self):
        p = policy()
        self.assertEqual(self.evaluate(p), AlarmDecision.AWAITING_CONFIRMATION)
        self.assertEqual(self.evaluate(p, age_seconds=20), AlarmDecision.READY)
        self.assertEqual(self.evaluate(p, age_seconds=10), AlarmDecision.DUPLICATE)

    def test_same_snapshot_retry_does_not_count_as_confirmation(self):
        p = policy()
        self.assertEqual(self.evaluate(p), AlarmDecision.AWAITING_CONFIRMATION)
        self.assertEqual(self.evaluate(p), AlarmDecision.AWAITING_CONFIRMATION)
        self.assertEqual(self.evaluate(p, age_seconds=20), AlarmDecision.READY)

    def test_drop_below_threshold_resets_confirmation(self):
        p = policy()
        self.assertEqual(self.evaluate(p), AlarmDecision.AWAITING_CONFIRMATION)
        self.assertEqual(self.evaluate(p, cost="3999"), AlarmDecision.BELOW_THRESHOLD)
        self.assertEqual(self.evaluate(p, age_seconds=20), AlarmDecision.AWAITING_CONFIRMATION)

    def test_partial_pricing_blocks_alarm(self):
        p = policy()
        self.assertEqual(
            self.evaluate(p, coverage=".999"),
            AlarmDecision.BLOCKED_INCOMPLETE_PRICING,
        )

    def test_stale_or_future_data_blocks_alarm(self):
        p = policy()
        self.assertEqual(
            self.evaluate(p, age_seconds=601),
            AlarmDecision.BLOCKED_STALE_DATA,
        )
        self.assertEqual(
            self.evaluate(p, age_seconds=-1),
            AlarmDecision.BLOCKED_STALE_DATA,
        )

    def test_limit_version_change_gets_new_deduplication_key(self):
        v1, v2 = policy(version=1), policy(version=2)
        self.evaluate(v1); self.assertEqual(self.evaluate(v1, age_seconds=20), AlarmDecision.READY)
        self.assertEqual(self.evaluate(v2), AlarmDecision.AWAITING_CONFIRMATION)
        self.assertEqual(self.evaluate(v2, age_seconds=20), AlarmDecision.READY)

    def test_exact_threshold_is_eligible(self):
        p = policy()
        self.assertEqual(self.evaluate(p, cost="4000"), AlarmDecision.AWAITING_CONFIRMATION)
        self.assertEqual(self.evaluate(p, cost="4000", age_seconds=20), AlarmDecision.READY)


if __name__ == "__main__":
    unittest.main()
