import unittest
from datetime import date, datetime, timezone

from pydantic import ValidationError
from fastapi import HTTPException

from api import CollectionScheduleUpdate, _reporting_window, _storage_pricing_summary


class ApiModelTests(unittest.TestCase):
    def test_collection_schedule_accepts_supported_bounds(self):
        self.assertEqual(CollectionScheduleUpdate(enabled=True, interval_seconds=120).interval_seconds, 120)
        self.assertEqual(CollectionScheduleUpdate(enabled=True, interval_seconds=600).interval_seconds, 600)

    def test_collection_schedule_rejects_gap_causing_cadence(self):
        with self.assertRaises(ValidationError):
            CollectionScheduleUpdate(enabled=True, interval_seconds=601)

    def test_storage_summary_counts_failures_without_resource_ids(self):
        resources = [{"provider_resource_type": "ebs", "provider_resource_id": "volume"},
                     {"provider_resource_type": "efs", "provider_resource_id": "filesystem"}]
        prices = {("ebs", "volume"): {"status": "COMPLETE"},
                  ("efs", "filesystem"): {"status": "UNRESOLVED", "reason": "no rate"}}
        summary = _storage_pricing_summary(resources, prices)
        self.assertEqual(summary["ebs"]["complete"], 1)
        self.assertEqual(summary["efs"]["unresolved_reasons"], {"no rate": 1})
        self.assertNotIn("filesystem", str(summary))

    def test_reporting_window_is_inclusive_and_capped_at_now(self):
        now = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
        start, end = _reporting_window(date(2026, 9, 1), date(2026, 9, 14), now)
        self.assertEqual(start, datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertEqual(end, now)

    def test_reporting_window_rejects_unordered_or_long_ranges(self):
        now = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
        for start, end in ((date(2026, 9, 2), date(2026, 9, 1)),
                           (date(2026, 1, 1), date(2026, 9, 1))):
            with self.assertRaises(HTTPException):
                _reporting_window(start, end, now)


if __name__ == "__main__":
    unittest.main()
