import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from pydantic import ValidationError
from fastapi import HTTPException

from api import (CollectionScheduleUpdate, PilotLimitCreate, _compile_filter,
                 _five_minute_window, _reporting_window, _storage_pricing_summary,
                 _team_baseline_for_window, _validate_filter_expression)


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

    def test_five_minute_trend_window_is_bounded_to_seven_days(self):
        now = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
        start, end = _five_minute_window(now, 10_080)
        self.assertEqual(end, now)
        self.assertEqual((end - start).total_seconds(), 7 * 24 * 3600)
        for minutes in (59, 10_081):
            with self.assertRaises(HTTPException):
                _five_minute_window(now, minutes)

    def test_team_filter_normalizes_nested_and_or_groups(self):
        value = _validate_filter_expression({"kind": "group", "operator": "AND", "conditions": [
            {"kind": "tag", "key": " Team ", "operator": "EQUALS", "value": " HPC "},
            {"kind": "group", "operator": "OR", "conditions": [
                {"kind": "tag", "key": "Environment", "operator": "EQUALS", "value": "prod"},
                {"kind": "tag", "key": "CostCenter", "operator": "EXISTS"},
            ]},
        ]})
        self.assertEqual(value["conditions"][0]["key"], "Team")
        self.assertEqual(value["conditions"][1]["operator"], "OR")

    def test_team_filter_rejects_empty_values_and_excessive_rules(self):
        with self.assertRaises(ValueError):
            _validate_filter_expression({"kind": "tag", "key": "Team", "operator": "EQUALS", "value": ""})
        with self.assertRaises(ValueError):
            _validate_filter_expression({"kind": "group", "operator": "OR", "conditions": [
                {"kind": "tag", "key": f"key-{index}", "operator": "EXISTS"}
                for index in range(21)
            ]})

    def test_team_filter_sql_uses_bound_parameters(self):
        expression = _validate_filter_expression({"kind": "group", "operator": "AND", "conditions": [
            {"kind": "tag", "key": "Team'; DROP TABLE resources; --", "operator": "EQUALS", "value": "HPC"},
            {"kind": "tag", "key": "Environment", "operator": "NOT_EQUALS", "value": "dev"},
        ]})
        sql, arguments = _compile_filter(expression, "c.tags", "test")
        self.assertNotIn("DROP TABLE", sql)
        self.assertIn(":test_key_0", sql)
        self.assertEqual(arguments["test_key_0"], "Team'; DROP TABLE resources; --")
        self.assertIn("c.tags ? :test_key_1", sql)

    def test_team_model_rejects_unbounded_or_invalid_filter(self):
        for name, expression in (
            ("HPC", {"kind": "group", "operator": "XOR", "conditions": []}),
            ("   ", {"kind": "tag", "key": "Team", "operator": "EXISTS"}),
        ):
            with self.assertRaises(ValidationError):
                PilotLimitCreate(name=name, amount_usd="100", filter_expression=expression)

    def test_team_model_accepts_zero_or_positive_declared_baseline(self):
        expression = {"kind": "group", "operator": "AND", "conditions": [
            {"kind": "tag", "key": "Team", "operator": "EQUALS", "value": "HPC"},
        ]}
        self.assertEqual(PilotLimitCreate(
            name="HPC", amount_usd="100", filter_expression=expression,
        ).baseline_amount_usd, Decimal("0"))
        self.assertEqual(PilotLimitCreate(
            name="HPC", amount_usd="100", baseline_amount_usd="42.123456",
            filter_expression=expression,
        ).baseline_amount_usd, Decimal("42.123456"))
        self.assertTrue(PilotLimitCreate(
            name="HPC", amount_usd="100", allow_partial_alerts=True,
            filter_expression=expression,
        ).allow_partial_alerts)
        with self.assertRaises(ValidationError):
            PilotLimitCreate(name="HPC", amount_usd="100",
                             baseline_amount_usd="-0.01",
                             filter_expression=expression)

    def test_declared_baseline_applies_only_to_creation_month_mtd_window(self):
        team = {"created_at": datetime(2026, 9, 14, 12, tzinfo=timezone.utc),
                "baseline_amount_usd": "75.25"}
        self.assertEqual(_team_baseline_for_window(
            team, datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 20, tzinfo=timezone.utc)), Decimal("75.25"))
        self.assertEqual(_team_baseline_for_window(
            team, datetime(2026, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 20, tzinfo=timezone.utc)), Decimal("0"))


if __name__ == "__main__":
    unittest.main()
