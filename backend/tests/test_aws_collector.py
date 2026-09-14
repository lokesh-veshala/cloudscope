import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from providers.aws.collector import (
    AwsAccountConfig,
    _ebs_volumes,
    _ec2_instances,
    _efs,
    _fsx,
    price_running_ec2,
    publish_sns_test,
    publish_threshold_alert,
    validate_connection,
)


class Paginator:
    def __init__(self, pages): self.pages = pages
    def paginate(self, **_): return self.pages


class Client:
    def __init__(self, pages): self.pages = pages
    def get_paginator(self, _): return Paginator(self.pages)


class IdentityClient:
    def __init__(self, account): self.account = account
    def get_caller_identity(self): return {"Account": self.account}


class GenericClient:
    def describe_instances(self, **_): return {}
    def list_metrics(self, **_): return {}
    def get_resources(self, **_): return {}


class SnsClient:
    def __init__(self, response=None, error=None):
        self.response = response or {"MessageId": "message-example"}
        self.error = error
        self.request = None
    def publish(self, **kwargs):
        self.request = kwargs
        if self.error:
            raise self.error
        return self.response


class Session:
    def __init__(self, account): self.account = account
    def client(self, name, **_):
        if name == "sts": return IdentityClient(self.account)
        return GenericClient()


class CollectorTests(unittest.TestCase):
    @patch("providers.aws.collector.session_for", side_effect=RuntimeError("private detail"))
    def test_profile_load_failure_is_reported_without_secret_detail(self, session):
        result = validate_connection(AwsAccountConfig("123456789012", "us-east-1", "missing"))
        self.assertFalse(result["connected"])
        self.assertEqual(result["checks"][0]["name"], "profile")
        self.assertNotIn("private detail", str(result))

    @patch("providers.aws.collector.Ec2OnDemandCatalog.fetch_linux_shared")
    @patch("providers.aws.collector.session_for")
    def test_cloudwatch_arguments_match_installed_sdk(self, session_for, price):
        import botocore.session
        from botocore.validate import validate_parameters
        shape = botocore.session.get_session().get_service_model("cloudwatch").operation_model("ListMetrics").input_shape
        client = GenericClient()
        client.list_metrics = lambda **kwargs: validate_parameters(kwargs, shape)
        session = Session("123456789012")
        original = session.client
        session.client = lambda name, **kwargs: client if name == "cloudwatch" else original(name, **kwargs)
        session_for.return_value = session
        result = validate_connection(AwsAccountConfig("123456789012", "us-east-1", "test"))
        self.assertTrue(result["connected"])

    def test_ec2_normalizes_purchase_model_os_and_tags(self):
        now = datetime.now(timezone.utc)
        pages = [{"Reservations":[{"Instances":[{
            "InstanceId":"i-example", "InstanceType":"c7i.large",
            "PlatformDetails":"Linux/UNIX",
            "InstanceLifecycle":"spot", "State":{"Name":"running"},
            "Placement":{"AvailabilityZone":"us-east-1a"},
            "Tags":[{"Key":"Name","Value":"worker"},{"Key":"Team","Value":"HPC"}],
        }]}]}]
        row = list(_ec2_instances(Client(pages), "us-east-1", now))[0]
        self.assertEqual(row["name"], "worker")
        self.assertEqual(row["metadata"]["purchase_model"], "SPOT")
        self.assertEqual(row["metadata"]["os"], "Linux")
        self.assertEqual(row["metadata"]["tags"]["Team"], "HPC")

    def test_ebs_keeps_billable_dimensions(self):
        now = datetime.now(timezone.utc)
        pages = [{"Volumes":[{"VolumeId":"vol-example","State":"in-use",
            "AvailabilityZone":"us-east-1a","VolumeType":"gp3","Size":100,
            "Iops":6000,"Throughput":250}]}]
        row = list(_ebs_volumes(Client(pages), "us-east-1", now))[0]
        self.assertEqual(row["metadata"], {"volume_type":"gp3","size_gib":100,"iops":6000,"throughput":250,"tags":{}})

    def test_efs_keeps_storage_class_bytes(self):
        now = datetime.now(timezone.utc)
        pages = [{"FileSystems":[{"FileSystemId":"fs-example",
            "LifeCycleState":"available", "SizeInBytes":{"Value":15,
            "ValueInStandard":10,"ValueInIA":4,"ValueInArchive":1},
            "ThroughputMode":"bursting"}]}]
        row = list(_efs(Client(pages), "us-east-1", now))[0]
        self.assertEqual(row["metadata"]["size_standard_bytes"], 10)
        self.assertEqual(row["metadata"]["size_ia_bytes"], 4)
        self.assertEqual(row["metadata"]["size_archive_bytes"], 1)

    def test_fsx_keeps_deployment_and_throughput_dimensions(self):
        now = datetime.now(timezone.utc)
        pages = [{"FileSystems":[{"FileSystemId":"fsx-example", "Lifecycle":"AVAILABLE",
            "FileSystemType":"LUSTRE", "StorageCapacity":1200, "StorageType":"SSD",
            "LustreConfiguration":{"DeploymentType":"PERSISTENT_2",
                "PerUnitStorageThroughput":250}}]}]
        row = list(_fsx(Client(pages), "us-east-1", now))[0]
        self.assertEqual(row["metadata"]["deployment_type"], "PERSISTENT_2")
        self.assertEqual(row["metadata"]["per_unit_storage_throughput"], 250)
        self.assertIsNone(row["metadata"]["drive_cache_type"])

    @patch("providers.aws.collector.session_for")
    def test_sns_test_is_explicit_and_contains_stable_event_id(self, session_for):
        client = SnsClient()
        session_for.return_value.client.return_value = client
        result = publish_sns_test(AwsAccountConfig("123456789012", "us-east-1", "test"),
            "arn:aws:sns:us-east-1:123456789012:cloudscope-test", "Test account",
            datetime(2026, 9, 14, tzinfo=timezone.utc), "event-example")
        self.assertTrue(result["published"])
        self.assertIn('"is_test":true', client.request["Message"])
        self.assertIn('"automatic_threshold_alert":false', client.request["Message"])
        self.assertIn('"event_id":"event-example"', client.request["Message"])

    @patch("providers.aws.collector.session_for")
    def test_sns_failure_is_sanitized(self, session_for):
        session_for.side_effect = RuntimeError("certificate secret detail")
        result = publish_sns_test(AwsAccountConfig("123456789012", "us-east-1", "test"),
            "arn:aws:sns:us-east-1:123456789012:cloudscope-test", "Test account",
            datetime(2026, 9, 14, tzinfo=timezone.utc), "event-example")
        self.assertFalse(result["published"])
        self.assertNotIn("certificate secret detail", result["error"])

    @patch("providers.aws.collector.session_for")
    def test_automatic_threshold_payload_is_not_a_test(self, session_for):
        client = SnsClient()
        session_for.return_value.client.return_value = client
        payload = {"event_type":"COST_THRESHOLD_EXCEEDED",
                   "event_id":"stable-event", "threshold_percent":"80",
                   "is_test":False, "automatic_threshold_alert":True}
        result = publish_threshold_alert(
            AwsAccountConfig("123456789012", "us-east-1", "test"),
            "arn:aws:sns:us-east-1:123456789012:cloudscope-test", payload)
        self.assertTrue(result["published"])
        self.assertIn('"automatic_threshold_alert":true', client.request["Message"])
        self.assertIn('"is_test":false', client.request["Message"])
        self.assertEqual(client.request["MessageAttributes"]["event_type"]["StringValue"],
                         "COST_THRESHOLD_EXCEEDED")

    @patch("providers.aws.collector.Ec2OnDemandCatalog.fetch_linux_shared")
    @patch("providers.aws.collector.session_for")
    def test_account_mismatch_fails_connection(self, session_for, price):
        session_for.return_value = Session("999999999999")
        price.return_value = object()
        result = validate_connection(AwsAccountConfig("123456789012", "us-east-1", "test"))
        self.assertFalse(result["connected"])
        self.assertIn("expected account", result["checks"][0]["error"])

    @patch("providers.aws.collector.Ec2OnDemandCatalog.fetch_linux_shared")
    @patch("providers.aws.collector.session_for")
    def test_spot_fallback_is_discounted_and_alarm_ineligible(self, session_for, fetch):
        session_for.return_value = Session("123456789012")
        fetch.return_value = type("Price", (), {
            "usd_per_unit": Decimal("0.10"),
            "sku":"sku", "rate_code":"rate",
            "effective_at":datetime.now(timezone.utc),
            "fetched_at":datetime.now(timezone.utc),
        })()
        rows = [{"provider_resource_type":"ec2","provider_resource_id":"i-spot",
                 "state":"running","metadata":{"purchase_model":"SPOT","os":"Linux","tenancy":"default","instance_type":"t3.micro"}}]
        result = price_running_ec2(AwsAccountConfig("123456789012","us-east-1","test"), rows)
        self.assertEqual(result["i-spot"]["status"], "ESTIMATED")
        self.assertEqual(result["i-spot"]["usd_per_hour"], "0.0580")
        self.assertEqual(result["i-spot"]["assumed_discount_percent"], "42")
        self.assertFalse(result["i-spot"]["alert_eligible"])


if __name__ == "__main__": unittest.main()
