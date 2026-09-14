from __future__ import annotations

from decimal import Decimal
from typing import Any

from providers.aws.pricing_catalog import CatalogMatchError, StorageCatalog


GIB = Decimal(2) ** 30
EBS_TYPES = {"standard", "gp2", "gp3", "st1", "sc1"}


def _component(name, quantity, price):
    return {"name": name, "quantity": str(quantity), "unit": price.unit,
            "usd_per_unit_month": str(price.usd_per_unit), "sku": price.sku,
            "rate_code": price.rate_code, "effective_at": price.effective_at.isoformat(),
            "fetched_at": price.fetched_at.isoformat()}


def ebs_quote(catalog: StorageCatalog, region: str, metadata: dict[str, Any]) -> dict:
    volume_type = metadata.get("volume_type")
    size = Decimal(str(metadata.get("size_gib")))
    if volume_type not in EBS_TYPES or size <= 0:
        raise CatalogMatchError("unsupported or invalid EBS volume dimensions")
    base = catalog.fetch_one(service_code="AmazonEC2", region_code=region,
        attributes={"productFamily": "Storage", "volumeApiName": volume_type}, unit="GB-Mo")
    components = [_component("storage", size, base)]
    total = size * base.usd_per_unit
    iops = Decimal(str(metadata.get("iops") or 0))
    if volume_type == "gp3":
        billable_iops = max(Decimal(0), iops - Decimal(3000))
        if billable_iops:
            rate = catalog.fetch_one(service_code="AmazonEC2", region_code=region,
                attributes={"productFamily": "Provisioned IOPS", "volumeApiName": volume_type},
                unit="IOPS-Mo")
            components.append(_component("provisioned_iops", billable_iops, rate))
            total += billable_iops * rate.usd_per_unit
    if volume_type == "gp3":
        throughput = Decimal(str(metadata.get("throughput") or 0))
        billable_throughput = max(Decimal(0), throughput - Decimal(125))
        if billable_throughput:
            rate = catalog.fetch_one(service_code="AmazonEC2", region_code=region,
                attributes={"productFamily": "Provisioned Throughput", "volumeApiName": "gp3"},
                unit="MBps-Mo")
            components.append(_component("provisioned_throughput", billable_throughput, rate))
            total += billable_throughput * rate.usd_per_unit
    return _quote("COMPLETE", "EBS_PROVISIONED", total, components,
                  proration="FIXED_30_DAY_MONTH")


def efs_quote(catalog: StorageCatalog, region: str, metadata: dict[str, Any]) -> dict:
    if metadata.get("availability_zone_name"):
        raise CatalogMatchError("EFS One Zone storage pricing is not implemented")
    classes = (("standard", "size_standard_bytes", "TimedStorage-ByteHrs"),
               ("infrequent_access", "size_ia_bytes", "IATimedStorage-ByteHrs"),
               ("archive", "size_archive_bytes", "ArchiveTimedStorage-ByteHrs"))
    if all(metadata.get(field) is None for _, field, _ in classes):
        raise CatalogMatchError("EFS storage-class byte counts are unavailable")
    total = Decimal(0)
    components = []
    for name, field, suffix in classes:
        size = Decimal(str(metadata.get(field) or 0)) / GIB
        if not size:
            continue
        rate = catalog.fetch_one(service_code="AmazonEFS", region_code=region,
            attributes={"productFamily": "Storage"}, unit="GB-Mo",
            usage_type_suffix=suffix)
        components.append(_component(name, size, rate))
        total += size * rate.usd_per_unit
    if not components:
        raise CatalogMatchError("EFS metered storage is zero or unavailable")
    return _quote("PARTIAL", "EFS_STORAGE_PARTIAL", total, components,
                  "throughput, access and transfer charges are excluded",
                  proration="UTC_CALENDAR_MONTH")


def fsx_quote(catalog: StorageCatalog, region: str, metadata: dict[str, Any]) -> dict:
    filesystem = metadata.get("filesystem_type")
    storage_type = metadata.get("storage_type")
    capacity = Decimal(str(metadata.get("storage_capacity_gib")))
    deployment = _fsx_deployment(metadata.get("deployment_type"))
    names = {"LUSTRE": "Lustre", "WINDOWS": "Windows", "ONTAP": "ONTAP", "OPENZFS": "OpenZFS"}
    if filesystem not in names or storage_type not in {"SSD", "HDD"} or capacity <= 0 or not deployment:
        raise CatalogMatchError("unsupported or incomplete FSx storage dimensions")
    attributes = {"productFamily": "Storage", "fileSystemType": names[filesystem],
                  "storageType": storage_type, "deploymentOption": deployment,
                  "operation": f"CreateFileSystem:{names[filesystem]}"}
    if filesystem == "LUSTRE":
        attributes["cacheType"] = metadata.get("drive_cache_type") or "N/A"
        throughput = metadata.get("per_unit_storage_throughput")
        if deployment == "Persistent":
            if not throughput:
                raise CatalogMatchError("FSx Lustre persistent throughput dimension is unavailable")
            attributes["throughputCapacity"] = str(throughput)
    rate = catalog.fetch_one(service_code="AmazonFSx", region_code=region,
        attributes=attributes, unit="GB-Mo")
    return _quote("PARTIAL", "FSX_STORAGE_PARTIAL", capacity * rate.usd_per_unit,
        [_component("storage", capacity, rate)],
        "throughput capacity, IOPS, backups and transfer charges are excluded",
        proration="FIXED_30_DAY_MONTH")


def _fsx_deployment(value):
    if not value:
        return None
    if value.startswith("SCRATCH_"):
        return "Single-AZ"
    if value.startswith("PERSISTENT_"):
        return "Persistent"
    if value.startswith("SINGLE_AZ"):
        return "Single-AZ"
    if value.startswith("MULTI_AZ"):
        return "Multi-AZ"
    return None


def _quote(status, source, total, components, limitation=None, *, proration):
    fetched = max((item["fetched_at"] for item in components), default=None)
    effective = max((item["effective_at"] for item in components), default=None)
    return {"status": status, "source": source, "monthly_usd": str(total),
            "components": components, "fetched_at": fetched, "effective_at": effective,
            "proration": proration, "alert_eligible": False, "limitation": limitation}
