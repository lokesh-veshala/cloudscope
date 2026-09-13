from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable

import boto3
from botocore.config import Config

from providers.aws.pricing_catalog import CatalogMatchError, Ec2OnDemandCatalog


AWS_CONFIG = Config(
    retries={"max_attempts": 8, "mode": "adaptive"},
    connect_timeout=5,
    read_timeout=30,
)


@dataclass(frozen=True)
class AwsAccountConfig:
    account_id: str
    region: str
    credential_profile: str


def session_for(config: AwsAccountConfig):
    return boto3.Session(
        profile_name=config.credential_profile,
        region_name=config.region,
    )


def validate_connection(config: AwsAccountConfig) -> dict[str, Any]:
    try:
        session = session_for(config)
    except Exception:
        return {"connected": False, "checks": [{"name": "profile", "status": "FAIL",
            "error": "AWS profile could not be loaded. Check the profile name, mounted configuration, and collector UID permissions."}]}
    checks: list[dict[str, Any]] = []

    def check(name: str, action: Callable[[], Any]) -> Any:
        try:
            value = action()
            checks.append({"name": name, "status": "PASS"})
            return value
        except Exception as exc:
            checks.append(
                {"name": name, "status": "FAIL", "error": _safe_error(exc)}
            )
            return None

    identity = check(
        "identity",
        lambda: session.client("sts", config=AWS_CONFIG).get_caller_identity(),
    )
    if identity and identity.get("Account") != config.account_id:
        checks[-1] = {
            "name": "identity",
            "status": "FAIL",
            "error": (
                f"expected account {config.account_id}; "
                f"received {identity.get('Account')}"
            ),
        }
    if not identity or identity.get("Account") != config.account_id:
        return {"connected": False, "checks": checks}
    check(
        "ec2_inventory",
        lambda: session.client("ec2", config=AWS_CONFIG).describe_instances(
            MaxResults=5
        ),
    )
    check(
        "cloudwatch",
        lambda: session.client("cloudwatch", config=AWS_CONFIG).list_metrics(
            Namespace="AWS/EC2"
        ),
    )
    check(
        "tagging",
        lambda: session.client(
            "resourcegroupstaggingapi", config=AWS_CONFIG
        ).get_resources(ResourcesPerPage=1),
    )
    check(
        "pricing",
        lambda: Ec2OnDemandCatalog(
            session.client("pricing", region_name="us-east-1", config=AWS_CONFIG)
        ).fetch_linux_shared(
            region_code=config.region,
            instance_type="t3.micro",
        ),
    )
    return {
        "connected": all(item["status"] == "PASS" for item in checks),
        "checks": checks,
    }


def collect_resources(
    config: AwsAccountConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    session = session_for(config)
    identity = session.client("sts", config=AWS_CONFIG).get_caller_identity()
    if identity.get("Account") != config.account_id:
        raise ValueError("Collector identity does not match the registered account")
    now = datetime.now(timezone.utc)
    resources: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    def guarded(service: str, action: Callable[[], Iterable[dict[str, Any]]]):
        try:
            batch = list(action())
            resources.extend(batch)
        except Exception as exc:
            failures.append({"service": service, "error": _safe_error(exc)})

    ec2 = session.client("ec2", config=AWS_CONFIG)
    guarded("EC2", lambda: _ec2_instances(ec2, config.region, now))
    guarded("EBS", lambda: _ebs_volumes(ec2, config.region, now))
    guarded("Elastic IP", lambda: _elastic_ips(ec2, config.region, now))
    guarded("NAT Gateway", lambda: _nat_gateways(ec2, config.region, now))
    guarded(
        "EFS",
        lambda: _efs(session.client("efs", config=AWS_CONFIG), config.region, now),
    )
    guarded(
        "FSx",
        lambda: _fsx(session.client("fsx", config=AWS_CONFIG), config.region, now),
    )
    guarded(
        "RDS",
        lambda: _rds(session.client("rds", config=AWS_CONFIG), config.region, now),
    )
    guarded(
        "ELB",
        lambda: _load_balancers(
            session.client("elbv2", config=AWS_CONFIG), config.region, now
        ),
    )
    guarded("S3", lambda: _s3(session.client("s3", config=AWS_CONFIG), now))
    return resources, failures


def price_running_ec2(
    config: AwsAccountConfig, resources: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    session = session_for(config)
    catalog = Ec2OnDemandCatalog(
        session.client("pricing", region_name="us-east-1", config=AWS_CONFIG)
    )
    result: dict[str, dict[str, Any]] = {}
    cache: dict[str, Any] = {}
    for resource in resources:
        if (
            resource["provider_resource_type"] != "ec2"
            or resource["state"] != "running"
        ):
            continue
        metadata = resource["metadata"]
        resource_id = resource["provider_resource_id"]
        if metadata.get("os") != "Linux":
            result[resource_id] = {
                "status": "UNRESOLVED",
                "reason": "unsupported_operating_system",
            }
            continue
        if metadata.get("tenancy") != "default":
            result[resource_id] = {
                "status": "UNRESOLVED",
                "reason": "non_shared_tenancy_requires_a_distinct_catalog_lookup",
            }
            continue
        instance_type = metadata["instance_type"]
        try:
            price = cache.get(instance_type)
            if price is None:
                price = catalog.fetch_linux_shared(
                    region_code=config.region,
                    instance_type=instance_type,
                )
                cache[instance_type] = price
            is_spot = metadata.get("purchase_model") == "SPOT"
            hourly_rate = (
                price.usd_per_unit * Decimal("0.58")
                if is_spot
                else price.usd_per_unit
            )
            result[resource_id] = {
                "status": "ESTIMATED" if is_spot else "COMPLETE",
                "usd_per_hour": str(hourly_rate),
                "sku": price.sku,
                "rate_code": price.rate_code,
                "effective_at": price.effective_at.isoformat(),
                "fetched_at": price.fetched_at.isoformat(),
                "source": (
                    "ASSUMED_SPOT_DISCOUNT" if is_spot else "AWS_PRICE_LIST"
                ),
                "assumed_discount_percent": "42" if is_spot else None,
                "alert_eligible": not is_spot,
            }
        except CatalogMatchError as exc:
            result[resource_id] = {
                "status": "UNRESOLVED",
                "reason": str(exc),
            }
    return result


def _base(
    resource_type,
    provider_type,
    resource_id,
    region,
    state,
    observed_at,
    *,
    arn=None,
    name=None,
    az=None,
    metadata=None,
):
    return {
        "resource_type": resource_type,
        "provider_resource_type": provider_type,
        "provider_resource_id": resource_id,
        "resource_arn": arn,
        "name": name or resource_id,
        "region": region,
        "availability_zone": az,
        "state": state,
        "observed_at": observed_at,
        "metadata": metadata or {},
    }


def _tags(items):
    return {item["Key"]: item["Value"] for item in items or []}


def _name(tags, fallback):
    return tags.get("Name", fallback)


def _ec2_instances(client, region, now):
    paginator = client.get_paginator("describe_instances")
    for page in paginator.paginate(PaginationConfig={"PageSize": 100}):
        for reservation in page.get("Reservations", []):
            for item in reservation.get("Instances", []):
                tags = _tags(item.get("Tags"))
                resource_id = item["InstanceId"]
                launch_time = item.get("LaunchTime")
                yield _base(
                    "compute.instance",
                    "ec2",
                    resource_id,
                    region,
                    item.get("State", {}).get("Name", "unknown"),
                    now,
                    name=_name(tags, resource_id),
                    az=item.get("Placement", {}).get("AvailabilityZone"),
                    metadata={
                        "instance_type": item.get("InstanceType"),
                        "purchase_model": (
                            "SPOT"
                            if item.get("InstanceLifecycle") == "spot"
                            else "ON_DEMAND"
                        ),
                        "os": "Windows" if item.get("Platform") == "windows" else "Linux",
                        "tenancy": item.get("Placement", {}).get("Tenancy", "default"),
                        "launch_time": launch_time.isoformat() if launch_time else None,
                        "tags": tags,
                    },
                )


def _ebs_volumes(client, region, now):
    for page in client.get_paginator("describe_volumes").paginate(
        PaginationConfig={"PageSize": 100}
    ):
        for item in page.get("Volumes", []):
            tags = _tags(item.get("Tags"))
            resource_id = item["VolumeId"]
            yield _base(
                "storage.volume",
                "ebs",
                resource_id,
                region,
                item.get("State", "unknown"),
                now,
                name=_name(tags, resource_id),
                az=item.get("AvailabilityZone"),
                metadata={
                    "volume_type": item.get("VolumeType"),
                    "size_gib": item.get("Size"),
                    "iops": item.get("Iops"),
                    "throughput": item.get("Throughput"),
                    "tags": tags,
                },
            )


def _elastic_ips(client, region, now):
    for item in client.describe_addresses().get("Addresses", []):
        resource_id = item.get("AllocationId") or item["PublicIp"]
        tags = _tags(item.get("Tags"))
        yield _base(
            "network.public_ip",
            "eip",
            resource_id,
            region,
            "associated" if item.get("AssociationId") else "unassociated",
            now,
            name=_name(tags, resource_id),
            metadata={
                "public_ip": item.get("PublicIp"),
                "association_id": item.get("AssociationId"),
                "tags": tags,
            },
        )


def _nat_gateways(client, region, now):
    for page in client.get_paginator("describe_nat_gateways").paginate():
        for item in page.get("NatGateways", []):
            resource_id = item["NatGatewayId"]
            tags = _tags(item.get("Tags"))
            yield _base(
                "network.nat_gateway",
                "nat",
                resource_id,
                region,
                item.get("State", "unknown"),
                now,
                name=_name(tags, resource_id),
                metadata={"connectivity_type": item.get("ConnectivityType"), "tags": tags},
            )


def _efs(client, region, now):
    for page in client.get_paginator("describe_file_systems").paginate():
        for item in page.get("FileSystems", []):
            resource_id = item["FileSystemId"]
            tags = _tags(item.get("Tags"))
            yield _base(
                "storage.filesystem",
                "efs",
                resource_id,
                region,
                item.get("LifeCycleState", "unknown"),
                now,
                name=item.get("Name") or _name(tags, resource_id),
                metadata={
                    "size_bytes": item.get("SizeInBytes", {}).get("Value"),
                    "performance_mode": item.get("PerformanceMode"),
                    "throughput_mode": item.get("ThroughputMode"),
                    "tags": tags,
                },
            )


def _fsx(client, region, now):
    for page in client.get_paginator("describe_file_systems").paginate():
        for item in page.get("FileSystems", []):
            resource_id = item["FileSystemId"]
            tags = _tags(item.get("Tags"))
            yield _base(
                "storage.filesystem",
                "fsx",
                resource_id,
                region,
                item.get("Lifecycle", "unknown"),
                now,
                arn=item.get("ResourceARN"),
                name=_name(tags, resource_id),
                az=item.get("AvailabilityZoneName"),
                metadata={
                    "filesystem_type": item.get("FileSystemType"),
                    "storage_capacity_gib": item.get("StorageCapacity"),
                    "storage_type": item.get("StorageType"),
                    "tags": tags,
                },
            )


def _rds(client, region, now):
    for page in client.get_paginator("describe_db_instances").paginate():
        for item in page.get("DBInstances", []):
            resource_id = item["DBInstanceIdentifier"]
            yield _base(
                "database.instance",
                "rds",
                resource_id,
                region,
                item.get("DBInstanceStatus", "unknown"),
                now,
                arn=item.get("DBInstanceArn"),
                name=resource_id,
                az=item.get("AvailabilityZone"),
                metadata={
                    "engine": item.get("Engine"),
                    "instance_class": item.get("DBInstanceClass"),
                    "multi_az": item.get("MultiAZ"),
                    "storage_type": item.get("StorageType"),
                    "allocated_storage_gib": item.get("AllocatedStorage"),
                },
            )


def _load_balancers(client, region, now):
    for page in client.get_paginator("describe_load_balancers").paginate():
        for item in page.get("LoadBalancers", []):
            arn = item["LoadBalancerArn"]
            zones = item.get("AvailabilityZones") or [{}]
            yield _base(
                "network.load_balancer",
                "elb",
                arn.rsplit("/", 1)[-1],
                region,
                item.get("State", {}).get("Code", "unknown"),
                now,
                arn=arn,
                name=item.get("LoadBalancerName"),
                az=zones[0].get("ZoneName"),
                metadata={"type": item.get("Type"), "scheme": item.get("Scheme")},
            )


def _s3(client, now):
    for item in client.list_buckets().get("Buckets", []):
        name = item["Name"]
        creation_date = item.get("CreationDate")
        yield _base(
            "storage.bucket",
            "s3",
            name,
            "global",
            "active",
            now,
            arn=f"arn:aws:s3:::{name}",
            name=name,
            metadata={
                "creation_date": creation_date.isoformat() if creation_date else None
            },
        )


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:500]
