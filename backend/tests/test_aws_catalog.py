import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cost_engine.models import UsageInterval
from cost_engine.pricing import CostEngine
from providers.aws.pricing_catalog import CatalogMatchError, Ec2OnDemandCatalog, SpotPriceCatalog


def document(price="0.272", rate="sku.term.rate"):
    return json.dumps({
        "product": {"sku": "sku", "attributes": {"instanceType": "c6g.2xlarge"}},
        "terms": {"OnDemand": {"sku.term": {
            "effectiveDate": "2026-09-01T00:00:00Z",
            "priceDimensions": {rate: {"unit": "Hrs", "pricePerUnit": {"USD": price}}},
        }}},
    })


class Client:
    def __init__(self, prices):
        self.prices = prices
        self.calls = []
    def get_products(self, **kwargs):
        self.calls.append(kwargs)
        return {"PriceList": self.prices}


class CatalogTests(unittest.TestCase):
    def test_exact_filters_and_decimal_price(self):
        client = Client([document()])
        price = Ec2OnDemandCatalog(client).fetch_linux_shared(
            region_code="us-east-1", instance_type="c6g.2xlarge"
        )
        filters = {item["Field"]: item["Value"] for item in client.calls[0]["Filters"]}
        self.assertEqual(filters["instanceType"], "c6g.2xlarge")
        self.assertEqual(filters["regionCode"], "us-east-1")
        self.assertEqual(filters["capacitystatus"], "Used")
        self.assertEqual(filters["marketoption"], "OnDemand")
        self.assertEqual(str(price.usd_per_unit), "0.272")

    def test_ambiguous_matches_fail_closed(self):
        client = Client([document(), document(rate="another.rate")])
        with self.assertRaises(CatalogMatchError):
            Ec2OnDemandCatalog(client).fetch_linux_shared(
                region_code="us-east-1", instance_type="c6g.2xlarge"
            )

    def test_missing_match_fails_closed(self):
        with self.assertRaises(CatalogMatchError):
            Ec2OnDemandCatalog(Client([])).fetch_linux_shared(
                region_code="us-east-1", instance_type="missing"
            )

    def test_spot_change_points_become_complete_intervals(self):
        start = datetime(2026, 9, 13, 8, tzinfo=timezone.utc)
        end = start + timedelta(hours=3)
        rows = [
            {"Timestamp": start - timedelta(minutes=10), "SpotPrice": "0.20"},
            {"Timestamp": start + timedelta(hours=1), "SpotPrice": "0.24"},
            {"Timestamp": start + timedelta(hours=2), "SpotPrice": "0.18"},
        ]
        prices = SpotPriceCatalog.to_intervals(rows, start=start, end=end)
        result = CostEngine().calculate(
            UsageInterval("i-spot", start, end), prices, require_complete=True
        )
        self.assertEqual(result.amount_usd, Decimal("0.62"))

    def test_missing_spot_price_at_start_leaves_coverage_gap(self):
        start = datetime(2026, 9, 13, 8, tzinfo=timezone.utc)
        end = start + timedelta(hours=2)
        rows = [{"Timestamp": start + timedelta(hours=1), "SpotPrice": "0.20"}]
        prices = SpotPriceCatalog.to_intervals(rows, start=start, end=end)
        result = CostEngine().calculate(UsageInterval("i-spot", start, end), prices)
        self.assertEqual(result.coverage, Decimal("0.5"))


if __name__ == "__main__":
    unittest.main()
