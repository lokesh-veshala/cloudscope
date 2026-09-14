import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from providers.aws.pricing_catalog import CatalogMatchError, CatalogPrice, StorageCatalog
from providers.aws.storage_pricing import ebs_quote, efs_quote, fsx_quote


NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


class FakeCatalog:
    def __init__(self): self.calls = []
    def fetch_one(self, **kwargs):
        self.calls.append(kwargs)
        family = kwargs["attributes"]["productFamily"]
        rates = {"Storage": Decimal("0.08"), "Provisioned IOPS": Decimal("0.005"),
                 "Provisioned Throughput": Decimal("0.04")}
        return CatalogPrice("sku", family, rates[family], kwargs["unit"], NOW, NOW, {})


def product(usage, unit="GB-Mo", rate="0.08"):
    return json.dumps({"product": {"sku": usage, "attributes": {"usagetype": usage}},
        "terms": {"OnDemand": {"term": {"effectiveDate": "2026-09-01T00:00:00Z",
        "priceDimensions": {"rate": {"unit": unit, "beginRange": "0",
        "pricePerUnit": {"USD": rate}}}}}}})


class Client:
    def __init__(self, rows): self.rows, self.calls = rows, []
    def get_products(self, **kwargs): self.calls.append(kwargs); return {"PriceList": self.rows}


class StoragePricingTests(unittest.TestCase):
    def test_gp3_included_and_additional_dimensions(self):
        catalog = FakeCatalog()
        quote = ebs_quote(catalog, "us-east-1", {"volume_type": "gp3",
            "size_gib": 100, "iops": 6000, "throughput": 250})
        self.assertEqual(quote["status"], "COMPLETE")
        self.assertEqual(Decimal(quote["monthly_usd"]), Decimal("28.000"))
        self.assertEqual([item["name"] for item in quote["components"]],
                         ["storage", "provisioned_iops", "provisioned_throughput"])

    def test_gp3_baseline_performance_is_not_charged(self):
        quote = ebs_quote(FakeCatalog(), "us-east-1", {"volume_type": "gp3",
            "size_gib": 100, "iops": 3000, "throughput": 125})
        self.assertEqual(Decimal(quote["monthly_usd"]), Decimal("8.00"))
        self.assertEqual(len(quote["components"]), 1)

    def test_tiered_iops_volume_fails_closed(self):
        with self.assertRaises(CatalogMatchError):
            ebs_quote(FakeCatalog(), "us-east-1", {"volume_type": "io2",
                "size_gib": 100, "iops": 50000, "throughput": None})

    def test_efs_class_bytes_are_priced_separately(self):
        gib = 2 ** 30
        quote = efs_quote(FakeCatalog(), "us-east-1", {"availability_zone_name": None,
            "size_standard_bytes": 10*gib, "size_ia_bytes": 2*gib,
            "size_archive_bytes": 0})
        self.assertEqual(quote["status"], "PARTIAL")
        self.assertEqual(len(quote["components"]), 2)

    def test_efs_one_zone_fails_closed(self):
        with self.assertRaises(CatalogMatchError):
            efs_quote(FakeCatalog(), "us-east-1", {"availability_zone_name": "us-east-1a",
                "size_standard_bytes": 1, "size_ia_bytes": 0, "size_archive_bytes": 0})

    def test_fsx_requires_deployment_specific_rate(self):
        catalog = FakeCatalog()
        quote = fsx_quote(catalog, "us-east-1", {"filesystem_type": "LUSTRE",
            "storage_type": "SSD", "storage_capacity_gib": 1200,
            "deployment_type": "PERSISTENT_2", "per_unit_storage_throughput": 250})
        self.assertEqual(quote["status"], "PARTIAL")
        attributes = catalog.calls[0]["attributes"]
        self.assertEqual(attributes["deploymentOption"], "Persistent")
        self.assertEqual(attributes["throughputCapacity"], "250")
        self.assertEqual(attributes["cacheType"], "N/A")

    def test_fsx_lustre_scratch_matches_single_az_catalog_dimension(self):
        catalog = FakeCatalog()
        fsx_quote(catalog, "us-east-1", {"filesystem_type": "LUSTRE",
            "storage_type": "SSD", "storage_capacity_gib": 1200,
            "deployment_type": "SCRATCH_2"})
        attributes = catalog.calls[0]["attributes"]
        self.assertEqual(attributes["deploymentOption"], "Single-AZ")
        self.assertNotIn("throughputCapacity", attributes)

    def test_usage_suffix_does_not_confuse_standard_with_ia(self):
        client = Client([product("USE1-IATimedStorage-ByteHrs"),
                         product("USE1-TimedStorage-ByteHrs")])
        catalog = StorageCatalog(client)
        price = catalog.fetch_one(service_code="AmazonEFS",
            region_code="us-east-1", attributes={"productFamily": "Storage"},
            unit="GB-Mo", usage_type_suffix="TimedStorage-ByteHrs")
        self.assertEqual(price.sku, "USE1-TimedStorage-ByteHrs")
        catalog.fetch_one(service_code="AmazonEFS",
            region_code="us-east-1", attributes={"productFamily": "Storage"},
            unit="GB-Mo", usage_type_suffix="TimedStorage-ByteHrs")
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
