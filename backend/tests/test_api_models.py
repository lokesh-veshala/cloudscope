import unittest

from pydantic import ValidationError

from api import CollectionScheduleUpdate


class ApiModelTests(unittest.TestCase):
    def test_collection_schedule_accepts_supported_bounds(self):
        self.assertEqual(CollectionScheduleUpdate(enabled=True, interval_seconds=120).interval_seconds, 120)
        self.assertEqual(CollectionScheduleUpdate(enabled=True, interval_seconds=600).interval_seconds, 600)

    def test_collection_schedule_rejects_gap_causing_cadence(self):
        with self.assertRaises(ValidationError):
            CollectionScheduleUpdate(enabled=True, interval_seconds=601)


if __name__ == "__main__":
    unittest.main()
