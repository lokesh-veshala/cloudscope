from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from cost_engine.models import PriceInterval


class CatalogMatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class CatalogPrice:
    sku: str
    rate_code: str
    usd_per_unit: Decimal
    unit: str
    effective_at: datetime
    fetched_at: datetime
    attributes: dict[str, str]


class Ec2OnDemandCatalog:
    """Strict AWS Price List adapter. It never selects the first ambiguous SKU."""

    SERVICE_CODE = "AmazonEC2"

    def __init__(self, pricing_client: Any) -> None:
        self.client = pricing_client

    def fetch_linux_shared(
        self, *, region_code: str, instance_type: str
    ) -> CatalogPrice:
        filters = [
            self._term("regionCode", region_code),
            self._term("instanceType", instance_type),
            self._term("operatingSystem", "Linux"),
            self._term("tenancy", "Shared"),
            self._term("preInstalledSw", "NA"),
            self._term("capacitystatus", "Used"),
            self._term("marketoption", "OnDemand"),
        ]
        documents: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            kwargs = {
                "ServiceCode": self.SERVICE_CODE,
                "Filters": filters,
                "FormatVersion": "aws_v1",
                "MaxResults": 100,
            }
            if token:
                kwargs["NextToken"] = token
            response = self.client.get_products(**kwargs)
            documents.extend(json.loads(item) for item in response.get("PriceList", []))
            token = response.get("NextToken")
            if not token:
                break
        candidates = self._hourly_candidates(documents)
        if any(not candidate.usd_per_unit.is_finite() or candidate.usd_per_unit <= 0 for candidate in candidates):
            raise CatalogMatchError("invalid or zero EC2 hourly rate")
        if len(candidates) != 1:
            raise CatalogMatchError(
                f"expected exactly one USD hourly price for {instance_type} in "
                f"{region_code}; received {len(candidates)}"
            )
        return candidates[0]

    @staticmethod
    def _term(field: str, value: str) -> dict[str, str]:
        return {"Type": "TERM_MATCH", "Field": field, "Value": value}

    @staticmethod
    def _hourly_candidates(documents: Iterable[dict[str, Any]]) -> list[CatalogPrice]:
        result: list[CatalogPrice] = []
        now = datetime.now(timezone.utc)
        for document in documents:
            product = document.get("product", {})
            sku = product.get("sku", "")
            for term in document.get("terms", {}).get("OnDemand", {}).values():
                effective = datetime.fromisoformat(
                    term["effectiveDate"].replace("Z", "+00:00")
                )
                for rate_code, dimension in term.get("priceDimensions", {}).items():
                    usd = dimension.get("pricePerUnit", {}).get("USD")
                    if usd is None or dimension.get("unit") != "Hrs":
                        continue
                    result.append(CatalogPrice(
                        sku=sku,
                        rate_code=rate_code,
                        usd_per_unit=Decimal(usd),
                        unit="Hrs",
                        effective_at=effective,
                        fetched_at=now,
                        attributes=dict(product.get("attributes", {})),
                    ))
        return result


class StorageCatalog:
    """Strict lookup for one On-Demand storage price dimension."""

    def __init__(self, pricing_client: Any) -> None:
        self.client = pricing_client
        self._cache: dict[tuple, CatalogPrice] = {}

    def fetch_one(self, *, service_code: str, region_code: str,
                  attributes: dict[str, str], unit: str,
                  usage_type_suffix: str | None = None) -> CatalogPrice:
        cache_key = (service_code, region_code, tuple(sorted(attributes.items())), unit,
                     usage_type_suffix)
        if cache_key in self._cache:
            return self._cache[cache_key]
        filters = [Ec2OnDemandCatalog._term("regionCode", region_code)]
        filters.extend(Ec2OnDemandCatalog._term(key, value)
                       for key, value in attributes.items())
        documents: list[dict[str, Any]] = []
        token = None
        while True:
            request: dict[str, Any] = {"ServiceCode": service_code,
                "Filters": filters, "FormatVersion": "aws_v1", "MaxResults": 100}
            if token:
                request["NextToken"] = token
            response = self.client.get_products(**request)
            documents.extend(json.loads(item) for item in response.get("PriceList", []))
            token = response.get("NextToken")
            if not token:
                break
        candidates = self._candidates(documents, unit, usage_type_suffix)
        if len(candidates) != 1:
            label = usage_type_suffix or ",".join(f"{k}={v}" for k, v in attributes.items())
            raise CatalogMatchError(
                f"expected one {service_code} {unit} rate for {label}; received {len(candidates)}")
        price = candidates[0]
        if not price.usd_per_unit.is_finite() or price.usd_per_unit <= 0:
            raise CatalogMatchError(f"invalid {service_code} {unit} rate")
        self._cache[cache_key] = price
        return price

    @staticmethod
    def _candidates(documents, unit, suffix):
        result = []
        now = datetime.now(timezone.utc)
        for document in documents:
            product = document.get("product", {})
            attributes = product.get("attributes", {})
            usage = attributes.get("usagetype", "")
            if suffix and not (usage == suffix or usage.endswith("-" + suffix)
                               or usage.endswith(":" + suffix)):
                continue
            for term in document.get("terms", {}).get("OnDemand", {}).values():
                effective = datetime.fromisoformat(term["effectiveDate"].replace("Z", "+00:00"))
                for rate_code, dimension in term.get("priceDimensions", {}).items():
                    usd = dimension.get("pricePerUnit", {}).get("USD")
                    if usd is None or dimension.get("unit") != unit:
                        continue
                    if str(dimension.get("beginRange", "0")) not in ("0", "0.0"):
                        continue
                    result.append(CatalogPrice(product.get("sku", ""), rate_code,
                        Decimal(usd), unit, effective, now, dict(attributes)))
        return result


class SpotPriceCatalog:
    """Retrieves timestamped Spot prices; the cost engine performs segmentation."""

    def __init__(self, ec2_client: Any) -> None:
        self.client = ec2_client

    def fetch(
        self,
        *,
        availability_zone: str,
        instance_type: str,
        product_description: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        if end <= start:
            raise ValueError("Spot price window must be non-empty")
        token: str | None = None
        rows: list[dict[str, Any]] = []
        while True:
            kwargs = {
                "AvailabilityZone": availability_zone,
                "InstanceTypes": [instance_type],
                "ProductDescriptions": [product_description],
                "StartTime": start,
                "EndTime": end,
                "MaxResults": 1000,
            }
            if token:
                kwargs["NextToken"] = token
            response = self.client.describe_spot_price_history(**kwargs)
            rows.extend(response.get("SpotPriceHistory", []))
            token = response.get("NextToken")
            if not token:
                break
        return sorted(rows, key=lambda item: item["Timestamp"])

    @staticmethod
    def to_intervals(
        rows: Iterable[dict[str, Any]], *, start: datetime, end: datetime
    ) -> list[PriceInterval]:
        """Convert AWS change points to half-open intervals.

        If AWS does not return a price active at the requested start, the leading
        gap remains unresolved and is caught by the cost engine's coverage gate.
        """
        ordered = sorted(rows, key=lambda item: item["Timestamp"])
        active = None
        changes: list[dict[str, Any]] = []
        for row in ordered:
            timestamp = row["Timestamp"]
            if timestamp <= start:
                active = row
            elif timestamp < end:
                changes.append(row)

        intervals: list[PriceInterval] = []
        cursor = start
        if active is None and changes:
            cursor = changes[0]["Timestamp"]
            active = changes.pop(0)
        for change in changes:
            if active is not None and change["Timestamp"] > cursor:
                intervals.append(PriceInterval(
                    cursor,
                    change["Timestamp"],
                    Decimal(active["SpotPrice"]),
                    f"spot:{active['Timestamp'].isoformat()}",
                ))
            active = change
            cursor = change["Timestamp"]
        if active is not None and cursor < end:
            intervals.append(PriceInterval(
                cursor,
                end,
                Decimal(active["SpotPrice"]),
                f"spot:{active['Timestamp'].isoformat()}",
            ))
        return intervals
