import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cost_engine.automatic import next_decision


NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)


class AutomaticAlertTests(unittest.TestCase):
    def decide(self, **changes):
        values = {"existing_count": 0, "last_data_as_of": None,
                  "event_exists": False, "data_as_of": NOW,
                  "blocked_reason": None, "cost": Decimal("80"),
                  "limit": Decimal("100"), "threshold": Decimal("80")}
        values.update(changes)
        return next_decision(**values)

    def test_two_distinct_eligible_observations_are_required(self):
        self.assertEqual(self.decide(), ("AWAITING_CONFIRMATION", 1))
        self.assertEqual(self.decide(existing_count=1,
            last_data_as_of=NOW-timedelta(minutes=2)), ("READY", 2))

    def test_same_observation_does_not_confirm(self):
        self.assertEqual(self.decide(existing_count=1, last_data_as_of=NOW),
                         ("AWAITING_CONFIRMATION", 1))

    def test_incomplete_cost_blocks_and_resets_confirmation(self):
        self.assertEqual(self.decide(existing_count=1,
            blocked_reason="unresolved"), ("BLOCKED", 0))

    def test_below_threshold_resets_and_exact_threshold_qualifies(self):
        self.assertEqual(self.decide(cost=Decimal("79.999")),
                         ("BELOW_THRESHOLD", 0))
        self.assertEqual(self.decide(cost=Decimal("80")),
                         ("AWAITING_CONFIRMATION", 1))

    def test_existing_event_is_deduplicated(self):
        self.assertEqual(self.decide(event_exists=True),
                         ("ALREADY_TRIGGERED", 2))

    def test_invalid_financial_input_fails_closed(self):
        self.assertEqual(self.decide(limit=Decimal("0")), ("BLOCKED", 0))
        self.assertEqual(self.decide(cost=Decimal("-1")), ("BLOCKED", 0))


if __name__ == "__main__":
    unittest.main()
